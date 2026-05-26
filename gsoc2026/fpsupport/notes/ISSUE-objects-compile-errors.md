# Root Cause: objects Compile Errors (14/14)

## Symptom
All 14 benchmarks in the `objects` suite report `COMPILE_ERR`.

## Diagnosis (2026-05-24)

### Bug Location
jpf-symbc's `Verifier.java` at `jpf-symbc/src/classes/org/sosy_lab/sv_benchmarks/Verifier.java` — missing `nondetObject()` method.

### Root Cause
The `objects` suite benchmarks call:
```java
A a = Verifier.nondetObject(A.class, new Factories.AFactory());
```

The sv-benchmarks reference `Verifier.java` (at `java/common/org/sosy_lab/sv_benchmarks/Verifier.java`) includes:
```java
public static <T> T nondetObject(Class<T> type, ObjectFactory<T> factory) {
    return factory.createObject();
}
```

However, the CI **skips** the reference `Verifier.java` and uses jpf-symbc's pre-built `Verifier.class` on the classpath. jpf-symbc's Verifier **lacked** the `nondetObject()` method, causing compilation to fail with:
```
error: cannot find symbol
    A a = Verifier.nondetObject(A.class, new Factories.AFactory());
                  ^
  symbol:   method nondetObject(Class<A>,AFactory)
  location: class Verifier
```

Additionally, the `ObjectFactory<T>` interface was missing from jpf-symbc's classpath (it exists only in the sv-benchmarks common/ directory as a source file that was previously also excluded by the `*/common/*` filter).

### Files Changed
- `jpf-symbc/src/classes/org/sosy_lab/sv_benchmarks/Verifier.java` — added `nondetObject()` method
- `jpf-symbc/src/classes/org/sosy_lab/sv_benchmarks/ObjectFactory.java` — new file with `ObjectFactory<T>` interface

### Implementation
```java
// In Verifier.java:
public static <T> T nondetObject(Class<T> type, ObjectFactory<T> factory) {
    return factory.createObject();
}

// ObjectFactory.java (new):
package org.sosy_lab.sv_benchmarks;
public interface ObjectFactory<T> {
    T createObject();
}
```

### Design Notes
- `nondetObject()` delegates to `factory.createObject()`, which internally uses `Verifier.nondet*()` methods
- The factory pattern generates objects with symbolic fields via the existing `Debug.makeSymbolic*()` API
- No changes needed to `Verifier.class` consumers — `nondetObject` is purely additive

### Verification
- `jpf-symbc` rebuilds successfully with `./gradlew :jpf-symbc:buildJars -x test`
- All 14 `objects` benchmarks compile and execute (some return UNKNOWN, which is expected for JPF)
- 0 compile errors in objects suite with the fix applied
