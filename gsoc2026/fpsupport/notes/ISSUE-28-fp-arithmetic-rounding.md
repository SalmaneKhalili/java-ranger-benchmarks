# Issue #28 — Model FP Arithmetic with Rounding

## What

Symbolic representation of IEEE-754 floating-point arithmetic: `FpAdd`, `FpSub`, `FpMul`, `FpDiv` expression nodes with rounding mode and precision sort. Bytecode handlers `FADD`/`FSUB`/`FMUL`/`FDIV` (and their double counterparts) create these nodes during symbolic execution.

## Files

| File | Role |
|------|------|
| `FpBinaryOp.java` | Abstract base: `left`, `right`, `FpSort`, `RoundingMode` |
| `FpAdd.java` | `a + b` with rounding |
| `FpSub.java` | `a - b` with rounding |
| `FpMul.java` | `a * b` with rounding |
| `FpDiv.java` | `a / b` with rounding; handles `/0` with special values |
| `FpSort.java` | Enum `FLOAT` (32-bit), `DOUBLE` (64-bit) |
| `RoundingMode.java` | Enum `NEAREST_TIES_TO_EVEN`, `DOWN`, `UP`, `TOWARD_ZERO` |
| `FADD.java` | Bytecode handler: pops two floats, pushes `FpAdd` |
| `FSUB.java` | Bytecode handler: pushes `FpSub` |
| `FMUL.java` | Bytecode handler: pushes `FpMul` |
| `FDIV.java` | Bytecode handler: pushes `FpDiv`; detects `/0` → `RealZero`/`RealInfinity`/`RealNaN` |
| `DADD.java` | Same as FADD but `FpSort.DOUBLE` |
| `DSUB.java` | Double subtraction |
| `DMUL.java` | Double multiplication |
| `DDIV.java` | Double division; same `/0` logic as FDIV |
| `RealExpression.java` | `_neg()` method (used by FNEG) — creates `BinaryRealExpression` not `FpSub` |

## Why This Design

### Expression nodes (FpAdd/FpSub/...) rather than opaque function calls

The alternative was to reuse existing `BinaryRealExpression` for FP operations (e.g., `BinaryRealExpression(a, PLUS, b)`). Rejected because:
- `BinaryRealExpression` has no `FpSort` or `RoundingMode`. Adding them to the base class would pollute every integer-arithmetic expression node (which doesn't need them).
- The solver backend needs to know it's dealing with FP arithmetic to emit Z3's `fp.add`/`fp.sub`/... rather than `+` on reals. Separate subclasses make type-dispatch trivial.
- Division-by-zero produces special values that would be invisible to a generic `BinaryRealExpression`.

### RoundingMode stored eagerly, not inferred

Java's default rounding is `NEAREST_TIES_TO_EVEN` for all basic operations. Storing `RoundingMode` in every node seems redundant, but:
- Future extensions may want to model `Math.round()` or other rounding modes.
- Z3's FP API requires a rounding mode argument for every operation. Having it pre-attached simplifies translation.
- The memory cost is negligible (one enum field).

### FDIV/DDIV handle division-by-zero symbolically, not through the solver

When the divisor is a constant zero, the bytecode handler immediately produces `RealInfinity` or `RealNaN` rather than creating an `FpDiv` node and hoping the solver figures it out. This is necessary because:
- The solver (Z3BitVector) doesn't support FP theory yet — it can't solve `x / 0`.
- Even with FP theory, division-by-zero has defined IEEE-754 results that don't require solving.
- The concrete result (stored in the stack slot) and the symbolic expression (attached as operand attribute) are both correctly set, so both concrete execution and future symbolic analysis see the right value.

### _neg() stays as BinaryRealExpression, not FpSub(0, x)

`RealExpression._neg()` returns `BinaryRealExpression(0, MINUS, this)` rather than `FpSub(RealConstant(0), this, ...)`. This is because `_neg()` is used by the existing `FNEG` bytecode handler, which wasn't changed by this issue. Creating an `FpSub` for negation would be more precise (it models IEEE-754 subtraction instead of real subtraction), but:
- The existing `BinaryRealExpression` pipeline (via `RealConstraint` and `PCParser`) already handles `0 - x` correctly for constraint solving.
- Changing `_neg()` to produce `FpSub` would break the solver path (which doesn't handle FpBinaryOp yet — see `PROGRESS.md`).
- This is a gap to fix later: FNEG should produce `FpSub(0, x, sort, RNE)` and `PCParser` should handle it.

### `solution()` method for concolic execution

Each `FpAdd`/`FpSub`/`FpMul`/`FpDiv` implements `solution()` returning the Java double/float result using standard operators. This is used by JPF's concolic mode (if enabled) and for debugging. The implementation follows IEEE-754 via Java's built-in semantics.

## Alternatives Considered

**1. Only handle arithmetic concretely (fall through to `super.execute()`).**

Status-quo before this issue. Rejected because it makes the symbolic representation incomplete: if a path constraint involves `fadd`, the resulting expression tree has no node for it, breaking the chain of symbolic operands. Downstream bytecodes (like `FCMPG`) would see no operand attribute and fall back to concrete comparison, losing symbolic coverage.

**2. Single `FpBinaryOp` with an operator enum instead of four subclasses.**

More concise, but the visitor pattern needs dispatch on specific operations. `preVisit(FpAdd)` and `preVisit(FpDiv)` may want to do different things (e.g., FpDiv needs to check for division-by-zero). Also, `FpDiv.solution()` has different NaN/infinity logic than `FpAdd.solution()`. Four subclasses keeps each simple.

**3. Lazy rounding (omit RoundingMode, default to RNE in the solver translator).**

Rejected because Z3's API requires an explicit rounding mode. If we omit it now, we'll need to thread it through later. Pre-emptively storing it avoids a refactor.

## Known Limitations

- **No solver support yet.** `PCParser.getExpression(RealExpression)` throws `RuntimeException` on any `FpBinaryOp` subclass. The solver path crashes when a constraint contains an FP expression. This is the root cause of the 4 failing CI benchmarks — see `PROGRESS.md`.
- `RealExpression._neg()` still produces `BinaryRealExpression` instead of `FpSub`. FNEG semantics are imprecise for NaN and -0.0.
- `FREM`/`DREM` (remainder) bytecodes are not handled — they fall through to concrete execution.
- Widening conversions (`I2D`, `L2F`, etc.) and narrowing conversions (`F2L`, `D2I`, etc.) don't use FP rounding models yet.

## Narrowing Conversions — F2L, D2I, F2I, D2L

### What

Narrowing conversions convert a floating-point value to an integer type with truncation toward zero (JVM spec §2.8.3). Java's semantics:
- Truncation toward zero (not rounding)
- NaN → 0
- ±Infinity → ±max/min int/long value
- Overflow → saturated to ±max/min

### Current State

All four bytecode handlers (`F2L`, `D2I`, `F2I`, `D2L`) follow the same broken pattern:

```java
pc._addDet(Comparator.EQ, sym_fval, sym_ival);
```

They create a `MixedConstraint` asserting equality between the `RealExpression` (the FP operand) and a fresh `SymbolicInteger` (the truncated result). This is semantically wrong — truncation toward zero is not equality. The constraint is also backwards: it asserts `fp_val == result` when narrowing means `result == trunc(fp_val)`.

**Solver translation of `mixed()`:**

`ProblemZ3BitVector.mixed()` (lines 1547-1593) has two paths:
1. `useFpForReals=true`: Converts the integer (bitvector) side to FP via `ctx.mkFPToFP(RTZ, bvExpr, sort)` and asserts `mkFPEq(fpExpr, converted)`. This constrains `fp_val == (float)int_val` — backwards. It tells the solver "the FP value must equal the result of converting this integer to float" instead of "the integer must equal the truncation of this FP value."
2. `useFpForReals=false`: Converts the bitvector to integer via `BV2Int`, then to real via `Int2Real`, and asserts `mkEq(realExpr, converted)`. Also backwards.

`ProblemZ3.mixed()` throws `RuntimeException` — narrowing is unsupported entirely with the non-bitvector solver.

**The crash point:** The 4 failing `jpf-regression` benchmarks (`ExSymExeF2L`, `FNEG`, `I2D`, `D2L`) crash because `PCParser.getExpression(RealExpression)` doesn't handle `FpBinaryOp` subclasses. When a constraint like `MixedConstraint(FpAdd(x, 1.0), EQ, SymbolicInteger)` from `(long)++x` hits `getExpression`, it throws `RuntimeException("## Error: Expression " + eRef)`. This propagates through `pc.simplify()` → `isSatisfiable()`, JPF records an unhandled exception, and the CI runner scores SAFE instead of UNSAFE.

### Plan

**Phase 1 — Wire up FpBinaryOp in PCParser (stop the crashes)**

Add `FpAdd`/`FpSub`/`FpMul`/`FpDiv` handling to `PCParser.getExpression(RealExpression)`. The dispatch follows the existing `BinaryRealExpression` pattern — call `pb.plus()`/`pb.minus()`/etc. These already handle FP in `ProblemZ3BitVector` when `useFpForReals=true`.

This unblocks the 4 failing CI benchmarks (100/104 on jpf-regression).

**Remaining issue**: `mixed()` still constrains backwards (`fp_val == (float)int_val`), which can produce spurious solutions.

**Phase 2 — Correct semantics with `FpToInt` expression**

Create a new expression node `FpToInt(realExpr, roundingMode, bitWidth)` extending `IntegerExpression` — same pattern as `RealIsNaN` (Issue #29).

1. **New expression**: `FpToInt.java` — `IntegerExpression` with `RealExpression`, `RoundingMode`, `bitWidth`. The result is `trunc(realExpr)` as an integer.
2. **Update bytecode handlers**: F2L/D2I/F2I/D2L create `FpToInt(realExpr, TOWARD_ZERO, 32/64)` instead of `_addDet(EQ, realExpr, sym_ival)`.
3. **Solver translation**: `ProblemZ3BitVector` emits `fp.to_sbv(realExpr, RTZ)`. `ProblemZ3` also needs support.
4. **PCParser**: Handle `FpToInt` in `getExpression(IntegerExpression)`.
5. **Visitor hooks**: Add `preVisit`/`postVisit`/`visitFpToInt` in `ConstraintExpressionVisitor`.

This gets `int_val == trunc(fp_val)` correct.
