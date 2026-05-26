# Issue #27 — IEEE-754 Special Values (NaN, infinities, signed zero)

## What

Representation of IEEE-754 special values (`NaN`, `±Infinity`, `±0.0`) as distinct symbolic expression nodes so the rest of the FP pipeline can recognize and reason about them.

## Files

| File | Role |
|------|------|
| `RealSpecialConstant.java` | Abstract base; extends `RealConstant` with an `FpSort` field |
| `RealNaN.java` | Singleton `FLOAT_NAN`, `DOUBLE_NAN` |
| `RealInfinity.java` | Factory `get(positive, sort)` |
| `RealZero.java` | Factory `get(positive, sort)`; preserves sign bit |
| `SpecialValueFactory.java` | Convenience for bytecode handlers |

## Why This Design

**Inheritance from `RealConstant` rather than `RealExpression`.**  
The existing `RealConstant` was already the standard way to represent concrete numeric values in the expression tree. All real-valued constants go through `RealConstant` (which extends `RealExpression`). By placing special values under `RealSpecialConstant → RealConstant`, they automatically fit wherever a `RealExpression` is expected (arithmetic operands, comparison operands, etc.) without needing to change every type signature. A `SymbolicReal` variable and a `RealNaN` constant can both be children of an `FpAdd` node — no special dispatch needed.

**Separate subclasses rather than a single `SpecialValue` with a tag enum.**  
Three reasons:
1. **`equals`/`hashCode` correctness** — `RealNaN(NaN)` and `RealInfinity(+Inf)` are different types with different semantics. Grouping them under one class with an enum tag would have made `equals` more error-prone (`FloatPOSITIVE_INFINITY == DoublePOSITIVE_INFINITY`? the tag-approach would need to check both the tag and the sort).
2. **Visitor dispatch** — `ConstraintExpressionVisitor` dispatches on concrete type. With separate classes, a downstream `postVisit(RealNaN)` can do something different from `postVisit(RealInfinity)`. With a single class, the visitor would need a switch on the tag, which is less extensible.
3. **Bytecode handler clarity** — `FDIV`/`DDIV` explicitly construct `RealZero.get(false, FpSort.FLOAT)` rather than `new SpecialValue(SpecialValue.Tag.NEGATIVE_ZERO, FpSort.FLOAT)`. The meaning is immediate.

**`RealSpecialConstant` holding the `FpSort` (FLOAT vs DOUBLE).**  
Without this, the solver translator would have no way to know whether a `RealNaN` should be emitted as `(_ NaN 8)` or `(_ NaN 11)` in Z3's FP theory. Storing the sort once at construction propagates correctly through all expression nodes.

**`RealZero` preserves the sign bit via `Double.longBitsToDouble(0x8000_...)`.**  
Java's `0.0 == -0.0` returns `true`, but the bit patterns are different and IEEE-754 division-by-zero distinguishes them (`1/+0 = +Inf`, `1/-0 = -Inf`). By storing the actual bit pattern, the concrete `solution()` method returns the correct signed zero, and future solver backends can emit the right Z3 literal.

## Alternatives Considered

**1. No special classes — just use `RealConstant(Double.NaN)` / `RealConstant(Double.POSITIVE_INFINITY)`.**  
Rejected because:
- `RealConstant(Double.NaN)` would compare equal to any other `RealConstant(Double.NaN)` via `Double.compare` — fine. But `instanceof` checks wouldn't work: there's no way to distinguish "this constant happens to be NaN" from "this is a deliberate NaN sentinel" without calling `Double.isNaN()`, which is fragile.
- More importantly, visitors and translators would have no hook to emit `(_ NaN 8)` vs `(fp #b0 #b11111111111 #x000...)` — they'd just emit `Double.NaN` as a real constant, which doesn't exist in the SMT real theory.

**2. Single `RealSpecialValue` class with an enum `{ NAN, POSITIVE_INFINITY, NEGATIVE_INFINITY, POSITIVE_ZERO, NEGATIVE_ZERO }`.**  
Rejected for the visitor-dispatch reason above.

**3. Store `FpSort` as a static thread-local.**  
Rejected because it doesn't compose: an expression tree mixing float and double would lose precision info. `FpSort` must be per-node.

## Known Limitations

- `SymbolicReal` does not yet carry an `FpSort`. This means `RealIsNaN.getSort()` can only determine the sort for constant operands (via `RealSpecialConstant`). This blocks correct SMT-LIB translation for mixed symbolic-constant comparisons until `SymbolicReal` is extended with a sort field.
- No solver backend currently knows how to emit Z3's FP theory for these constants — that's tracked under "Extend `ProblemZ3BitVector` for FP theory" in the parent issue.
