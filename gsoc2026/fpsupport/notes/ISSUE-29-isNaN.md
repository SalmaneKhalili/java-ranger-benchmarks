# Issue #29 — `isNaN` Predicate

## What

A symbolic `IntegerExpression` predicate `RealIsNaN(expr)` that evaluates to `1` when `expr` is NaN and `0` otherwise. This gives symbolic execution a handle on `Float.isNaN()` and `Double.isNaN()` so path conditions involving NaN checks are correctly modelled.

## Files

| File | Role |
|------|------|
| `RealIsNaN.java` | `IntegerExpression` subclass; factory `create(expr)` |
| `RealNaN.java` | NaN constant used by `isNaN` (from #27) |
| `ConstraintExpressionVisitor.java` | Visitor hooks `preVisit`, `visitRealIsNaN`, `postVisit` |

The native method peers `Float.isNaN` and `Double.isNaN` are in `jpf-core` (`JPF_java_lang_Float.java:54` and `JPF_java_lang_Double.java:59`) — they fall through to concrete `Float.isNaN(v)` / `Double.isNaN(v)`. The symbolic predicate (`RealIsNaN`) is used by the **comparison bytecodes** (FCMPG/FCMPL from #30), not directly by the native peers.

## Why This Design

### `RealIsNaN` extends `IntegerExpression`, not `RealExpression`

`isNaN(x)` returns a boolean, which JPF models as an integer (`0` or `1`). Extending `IntegerExpression` is the natural choice — it's how all boolean-valued predicates work in jpf-symbc. A `RealExpression` would be wrong because NaN checks don't produce a floating-point value.

### Factory method `create(expr)` instead of public constructor

Two reasons:
1. A caching opportunity (the `TODO` in the code): if the same `expr` appears in multiple `isNaN` checks, they could share one `RealIsNaN` instance, reducing AST size.
2. Clearer API: `RealIsNaN.create(x)` reads as "create an isNaN check for x".

### Visitor uses 3 hooks: `preVisit`, `visitRealIsNaN`, `postVisit`

Making `visitRealIsNaN` a separate step (rather than combining logic into `preVisit`/`postVisit`) is unusual but follows the existing pattern in `ConstraintExpressionVisitor`. The intention is that `visitRealIsNaN` does the main translation work while the surrounding hooks handle enter/exit bookkeeping. Currently no translator implements this (solver support is pending), so the pattern is established for future use.

### `getSort()` returns `FpSort` when determinable

The sort (float vs double) is needed to emit the correct Z3 `fp.isNaN` call. For constant operands (`RealNaN`, `RealInfinity`, `RealZero`), the sort is stored in `RealSpecialConstant`. For symbolic variables, `SymbolicReal` doesn't yet carry its sort — hence the `TODO` and `return null` fallback. This is a known gap.

## Alternatives Considered

**1. No symbolic predicate — rely on concrete `isNaN()` in the native peer.**

Status-quo before this issue. The problem: if `x` is symbolic, `Float.isNaN(x)` executes concretely on the current concrete value (which is `0.0` from `makeSymbolicReal`). It always returns `false`. Path conditions involving `if (Float.isNaN(x))` would never explore the NaN branch, producing unsound results. A symbolic predicate is necessary for correctness.

**2. Single `isNaN` node inside a broader `FpCmp` expression AST.**

The parent issue (#26) originally proposed an AST-based comparison node `FpCmp(a, b, pred)` that could embed `isNaN` checks. This was abandoned in favour of the 4-choice PCChoiceGenerator approach (#30). `RealIsNaN` is still useful independently — it can appear in bytecodes that directly call `Float.isNaN()` without a preceding comparison (e.g., `if (Float.isNaN(x))`).

**3. Inline `isNaN` as a `MathFunction` (`MathFunction.IS_NAN`).**

The existing `MathRealExpression` already supports function calls like `sin`, `cos`, `sqrt`. Adding `IS_NAN` there would be simpler. Rejected because:
- `MathRealExpression` extends `RealExpression` — `isNaN` returns boolean, not real.
- `MathRealExpression` doesn't interoperate with `IntegerExpression` constraints.
- The existing `MathFunction` enum and `MathRealExpression` visitor would need special-casing for the non-real return type.

## Known Limitations

- `getSort()` returns `null` when the inner expression is a `SymbolicReal`. This blocks correct Z3 `fp.isNaN` emission until `SymbolicReal` is extended with an `FpSort` field.
- No solver backend handles `RealIsNaN` yet — it's only an AST node.
- `RealIsNaN.create()` always allocates a new object — no deduplication.
- The `ConstraintExpressionVisitor` has a 3-phase hook (`preVisit`/`visitRealIsNaN`/`postVisit`) that no concrete visitor implements. If a visitor forgets to override `visitRealIsNaN`, the `preVisit` default (empty) and `postVisit` default (empty) run, and the inner expression is still visited — so the expression tree is traversed but no predicate translation happens. This is silent but harmless.
