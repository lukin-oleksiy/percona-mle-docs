# Java libraries for routines (a DBA guide)

Routines can use third-party Java libraries (jars) that the DBA installs. A jar serves routines in all three
languages: Java routines use its classes directly, and JavaScript and Python routines reach them through
`Java.type(...)` and `java.type(...)`. This page covers installing jars, using them, choosing a library for numeric
work, and the rules a library must follow to work well in MLE workers.

Jars need the JVM build: the Python-only native build has no Java.

## Installing a library

Jars come from Maven with the DBA's tool `mle_package` (in the `percona-mle-tools` package, or the tools Docker
image). It installs a library and its dependencies into `java/` below the package directory (`mle.package_dir`):

```sh
mle_package install java org.ejml:ejml-ddense:0.46.1
mle_package list
```

Your own jar works too: copy it into `<mle.package_dir>/java/`. The directory must not be writable by the group or
others (`mle_package` checks this): workers run what is in it.

Workers load the jars when they start, and a running worker keeps what it has. Then, in mysqld, while no session
holds a worker:

```sql
SELECT mle_packages_refresh();
```

New workers see the change. A session holding a worker can release it with `SELECT mle_session_reset();`.

## Using a library

**From Java,** by the full class name (a routine body has no `import` lines; a `CREATE LIBRARY ... LANGUAGE JAVA`
can have them). A least-squares trend line with EJML:

```sql
CREATE TABLE sales (day INT PRIMARY KEY, amount DOUBLE NOT NULL);
INSERT INTO sales VALUES (1, 10.5), (2, 12.1), (3, 13.8), (4, 16.2), (5, 17.9);

CREATE PROCEDURE sales_trend(OUT slope DOUBLE, OUT intercept DOUBLE) READS SQL DATA
LANGUAGE JAVA AS $$
List<Row> rows = Mle.session().runSql("SELECT day, amount FROM sales").fetchAll();
var a = new org.ejml.data.DMatrixRMaj(rows.size(), 2);   // columns: 1, day
var y = new org.ejml.data.DMatrixRMaj(rows.size(), 1);   // amount
for (int i = 0; i < rows.size(); i++) {
  a.set(i, 0, 1);
  a.set(i, 1, ((Number) rows.get(i).get(0)).doubleValue());
  y.set(i, 0, ((Number) rows.get(i).get(1)).doubleValue());
}
var x = new org.ejml.data.DMatrixRMaj(2, 1);
// More rows than columns: a least-squares solution.
if (!org.ejml.dense.row.CommonOps_DDRM.solve(a, y, x)) throw new IllegalArgumentException("no unique fit");
intercept[0] = x.get(0);
slope[0] = x.get(1);
$$;
CALL sales_trend(@slope, @intercept);
SELECT ROUND(@slope, 3) AS slope, ROUND(@intercept, 3) AS intercept;
```

```text
+-------+-----------+
| slope | intercept |
+-------+-----------+
|  1.89 |      8.43 |
+-------+-----------+
```

**From Python** (`java.type`):

```sql
CREATE FUNCTION py_inverse(m JSON) RETURNS JSON DETERMINISTIC LANGUAGE PYTHON AS $$
    import java
    DMatrixRMaj = java.type('org.ejml.data.DMatrixRMaj')
    CommonOps = java.type('org.ejml.dense.row.CommonOps_DDRM')
    n = len(m)
    a = DMatrixRMaj(n, n)
    for i in range(n):
        for j in range(n):
            a.set(i, j, float(m[i][j]))
    if not CommonOps.invert(a):
        raise ValueError('singular matrix')
    return [[round(a.get(i, j), 6) for j in range(n)] for i in range(n)]
$$;
SELECT py_inverse('[[4, 7], [2, 6]]') AS inverse;
```

```text
+----------------------------+
| inverse                    |
+----------------------------+
| [[0.6, -0.7], [-0.2, 0.4]] |
+----------------------------+
```

**From JavaScript** (`Java.type`):

```sql
CREATE FUNCTION js_det(m JSON) RETURNS DOUBLE DETERMINISTIC LANGUAGE JAVASCRIPT AS $$
  const DMatrixRMaj = Java.type('org.ejml.data.DMatrixRMaj')
  const CommonOps = Java.type('org.ejml.dense.row.CommonOps_DDRM')
  const a = new DMatrixRMaj(m.length, m.length)
  m.forEach((row, i) => row.forEach((v, j) => a.set(i, j, v)))
  return CommonOps.det(a)
$$;
SELECT js_det('[[4, 7], [2, 6]]') AS det;
```

```text
+------+
| det  |
+------+
|   10 |
+------+
```

## Choosing a library for numeric work

| Job | Use | Notes |
|---|---|---|
| Vectors: similarity, distances, the k nearest rows, related items | the built-in **vector helper** (`com.percona.mle.api.Vectors`, see the tutorial) | nothing to install; SIMD and threads; the same from all three languages |
| Arrays and numeric work in general | **numpy** in Python routines | the main tool; one thread is enough for most jobs |
| Linear algebra in Java (solving, inverting, decompositions) | **EJML** (Apache 2.0): `org.ejml:ejml-ddense` (`double`) or `ejml-fdense` (`float`) | use the single-thread operations (`CommonOps_DDRM`, `CommonOps_FDRM`), **not** the `_MT_` ones |
| | **ojAlgo** (MIT): `org.ojalgo:ojalgo` | call `org.ojalgo.OjAlgoUtils.limitThreadsTo(1)` first |
| Hot numeric code of your own | your own jar | see [Writing your own jar](#writing-your-own-jar) |

For large vector jobs, the vector helper is much faster than these libraries: related items for 10,000 vectors of
768 dimensions take a fraction of a second, against several seconds for EJML or ojAlgo even with all their threads.

## Rules for libraries in MLE workers

**1. A library must not leave threads behind.** A worker is kept after a session and reused by the same account's
next session, already warm. If a call leaves threads running, the worker can't be trusted any more, so it is
replaced. The call itself succeeds and nobody sees an error, but every later session starts with a new, cold worker:
a new JVM, nothing compiled yet, the jars loaded again.

- Many libraries start a thread pool and keep it. In our tests, EJML's `_MT_` operations and ojAlgo's default
  (parallel) mode do this, so the worker was replaced after every call.
- Parallel code that is fine: a pool the code creates and shuts down before it returns, or the JDK's shared pool
  (parallel streams, `ForkJoinPool.commonPool()`), which the worker treats as its own.
- `KILL` stops the routine but not threads a library started itself: they run until their work is done.

**2. A library's static state lasts as long as the worker.** Jar classes are loaded once per worker and stay
loaded for the account's later sessions. A static setting (such as ojAlgo's `limitThreadsTo(1)`), a cache or a
singleton made in one session is still there in the next. Don't keep data that one session must not see in another
in static fields.

**3. Memory.** A library's arrays live on the worker's Java heap: `mle.heap_max`, 512 MiB by default, within the
worker's whole limit `mle.memory_max` (2 GiB). 100,000 vectors of 768 floats take about 300 MB, and many libraries
make a copy on top (in `double`, or packed for speed). Raise `mle.heap_max` for large matrices.

**4. Put hot code in a jar, not in a routine body.** A jar's code stays compiled in the worker across the account's
sessions. A routine body is compiled again in every session, so its loops start slow each time.

## Checking a library

Call a routine that uses the library a few times, each time in a new session, and watch two status variables:

```sql
SELECT VARIABLE_NAME, VARIABLE_VALUE FROM performance_schema.global_status
 WHERE VARIABLE_NAME IN ('mle_workers_spawned', 'mle_worker_reuses');
```

Normally `mle_worker_reuses` grows with each session. If `mle_workers_spawned` grows instead, and
`mle_worker_reuses` doesn't, the library leaves threads behind and the worker is replaced after each call.

## Writing your own jar

- **Java 25,** like the workers. Classes in the JDK and in other installed jars are available. Routines can't call
  anything that the sandbox forbids (files, network, processes), and neither can a jar's code.
- **The Vector API** (`jdk.incubator.vector`) is enabled in workers; compile with `--add-modules
  jdk.incubator.vector`. Keep a vector species in a `static final` field and use it directly
  (`FloatVector.SPECIES_PREFERRED`): a species copied into a local variable and used inside a lambda ran about 16
  times slower in our tests.
- **Threads:** if the jar runs work in parallel, it must shut its pool down before it returns (rule 1), or use the
  JDK's shared pool.
- **Warm-up:** a jar's code is compiled while it runs. A worker's first calls are slower, often for the first one
  or two seconds of work, and later sessions of the same account get the compiled code.
