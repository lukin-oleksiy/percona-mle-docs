# How MLE uses GraalVM's polyglot runtime

MLE runs JavaScript, Python and Java stored programs on GraalVM. GraalVM is a JVM that can also host other
languages, here JavaScript (GraalJS) and Python (GraalPy), so that code in one language can call code in another
directly, inside one process. This page explains how MLE is built on that, and what users get from it.

## One worker, three languages

Routines don't run inside mysqld. Each runs in a **worker**: a separate process with its own sandbox
(Landlock, cgroup memory and process limits). Workers are reused by later sessions of the same account. A worker is
a single JVM:

```text
 mysqld + component_mle                         worker process (one JVM, sandboxed)
 ┌──────────────────────┐   shared memory   ┌───────────────────────────────────────────────────────┐
 │ CREATE FUNCTION ...  │ ◀───────────────▶ │  Java routines        polyglot Context                │
 │ LANGUAGE JAVASCRIPT  │   calls, SQL,     │  (compiled by javac)  ┌──────────────┬───────────────┐ │
 │        | PYTHON      │   results         │                       │ JavaScript   │ Python        │ │
 │        | JAVA        │                   │                       │ (GraalJS)    │ (GraalPy,     │ │
 │ SQL from routines    │                   │                       │              │  numpy)       │ │
 └──────────────────────┘                   │                       └──────────────┴───────────────┘ │
                                            │  shared Java core: SQL API · value codec ·             │
                                            │  vector helper · libraries · sandbox rules             │
                                            │  one JIT compiler (Graal) · one heap · one set of limits │
                                            └───────────────────────────────────────────────────────┘
```

- **Java routines** are ordinary Java classes, compiled with `javac` inside the worker and run by the JVM.
- **JavaScript and Python routines** run in one *polyglot context* per worker. The context is created at the first
  JavaScript or Python call, and Java routines can use it too.
- **Machine code for every language:** the Graal compiler compiles all three, so JavaScript and Python routines run
  as compiled code once they are warm, not only interpreted.

## What MLE builds on the polyglot runtime

**One SQL API, three languages.**
- `session.runSql(...)`, prepared statements, transactions and result sets are implemented once, in Java.
- JavaScript and Python see that implementation through thin *polyglot proxies*: `row.name`, `row['name']` and `row[0]` in
  JavaScript and Python, with snake_case method names (`run_sql`, `fetch_all`) in Python.
  Java routines use the same classes directly (`Mle.session()`).
- **Same behaviour everywhere:** limits, errors and transactions work the same in every language. A fix applies to
  all three. Reading a result row costs about the same in each language (a fraction of a microsecond).

**One conversion of values.** Arguments and results are decoded once, in Java, and handed to each language in its
own form:
- `VECTOR` becomes a `Float32Array`, a numpy array or a `float[]`;
- `DECIMAL` becomes a string or Python's `Decimal`;
- `JSON` becomes objects.

**One vector helper.**
- `com.percona.mle.api.Vectors` (dot products, cosine similarity, distances, the k nearest rows) is written once
  in Java, with SIMD instructions and threads.
- JavaScript and Python call it like a library of their own (`Java.type(...)`, `java.type(...)`), at the same speed
  as Java.
- JavaScript gets fast vector maths without numpy, and Python can use threads for its heaviest work.

**Libraries across languages.**
- `CREATE LIBRARY` stores a module in any of the three languages, and a routine in another language can import it
  (`USING`).
- The DBA's third-party packages work the same way:
  - Python modules such as numpy are reachable from JavaScript (`importPython('numpy')`) and Java
    (`Mle.importPython("numpy")`);
  - npm packages from Python (`import_js(...)`) and Java (`Mle.importJs(...)`);
  - Java jars from JavaScript and Python (`Java.type('...')`, `java.type('...')`).

```sql
CREATE LIBRARY geo LANGUAGE JAVA AS $$
package geo;
public final class Shapes {
  private Shapes() {}
  public static double diagonal(double w, double h) { return Math.sqrt(w * w + h * h); }
}
$$;
CREATE FUNCTION js_geo(w DOUBLE, h DOUBLE) RETURNS DOUBLE LANGUAGE JAVASCRIPT USING (geo)
  AS $$ return geo.Shapes.diagonal(w, h) $$;
CREATE FUNCTION js_numpy() RETURNS DOUBLE LANGUAGE JAVASCRIPT AS $$
  const np = importPython('numpy')
  return np.linalg.norm([3, 4])
$$;
SELECT js_geo(3, 4) AS java_from_javascript, js_numpy() AS numpy_from_javascript;
```

```text
+----------------------+-----------------------+
| java_from_javascript | numpy_from_javascript |
+----------------------+-----------------------+
|                    5 |                     5 |
+----------------------+-----------------------+
```

MLE converts values at these boundaries where the runtime alone would not: JavaScript and Java arrays become numpy
arrays when numpy functions get them (one memory copy for numbers), and numpy's numbers are plain numbers in
JavaScript and Java ([Languages together](../user-guide/polyglot.md) has the details).

**Sandboxing.** MLE uses the polyglot runtime's own access controls on top of the worker process's sandbox:
- JavaScript and Python can use Java objects, but not reflection, class loading, threads, processes or JVM
  management;
- they can't look up the worker's own classes;
- libraries come from a read-only virtual file system;
- they can't open network sockets or start processes.

A `KILL` interrupts the running code of any language. The process sandbox (Landlock, cgroups) remains the outer
boundary.

## What users get

- **Each language gets the others' strengths.** Python's numpy from JavaScript and Java; Java's speed (JIT, SIMD,
  threads) and its libraries from Python and JavaScript; npm packages from Python and Java.
- **Calls between languages are cheap.** They are in-process function calls: about a tenth of a microsecond, with
  no serialization and no second process.
- **One behaviour.** SQL, types, limits and errors are the same in every language, because one implementation
  serves all three.
- **Write once.** A library or helper written in one language serves routines in all three.
- **Less memory and fewer moving parts.** One process per worker with one heap, one compiler and one set of limits
  (`mle.memory_max`), instead of a runtime per language.

## Costs and limits

- **Values that cross languages are shared or converted.** Objects are shared (each language uses the other's object
  with its own syntax); arrays given to numpy are copied. APIs such as `Vectors.load(sql)` keep big
  data on one side (Java) and pass only results.
- **New workers start cold.** Code is compiled while it runs, so the first calls in a new worker are slower.
  Workers are therefore prepared while they wait in the pool (the vector helper by default, Python on request:
  `mle.pool_prewarm`), and kept and reused by the same account's later sessions.
- **The Python-only native build** is a Native Image: it starts faster and uses less memory, but it has no Java
  routines, no JavaScript, no Java libraries and no vector helper.
