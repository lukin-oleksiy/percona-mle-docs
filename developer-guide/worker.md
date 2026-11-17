<!-- check-page: skip: a description of the code -->
# The worker

The worker is a JVM (or a native image) started by `mle_worker` after its lockdown. Its Java code is in
`worker/src/main/java/com/percona/mle/`: `worker` (the implementation), `api` and `sql` (what routines see).

## Start

`Main.run` (called through JNI by the bootstrap) maps the mailbox, starts the `Control` thread, prepares what
`mle.pool_prewarm` asks for (`java`, `js`, `python`, `numpy` before `READY`; `vectors` after `READY`, while the
worker waits in the pool), sends `READY` with the languages it can run, and enters the dispatcher's loop. JVM workers
start from the JDK's AOT cache (classes and profiles recorded by `Train` at build time).

## Requests

**`Dispatcher`** handles the host's requests on the worker's main thread, one at a time: `PREPARE`, `CALL`, `DROP`,
`RESET`, `OPTIONS`, `PARSE_LIBRARY`.
- While a call runs, the routine's SQL and library imports are requests to the host (`SQL`, `LIBRARY`); the host may
  answer with a nested `CALL` or `PREPARE` (a routine called from that SQL), which the dispatcher handles recursively
  at depth + 1.
- Errors become `RESULT` statuses: a guest exception (with its frames, innermost first, and source lines), an
  interrupt, exhaustion (`OutOfMemoryError`, `StackOverflowError`, GraalPy's `MemoryError`: the worker is then
  tainted and retired), an internal error.
- After `RESET`, the worker collects garbage only once no request has come for 200 ms, so a session taking a reused
  worker doesn't wait for it.

| Class | Role |
|---|---|
| `Main` | the JNI entry: wiring, prewarm, `READY`, the loop |
| `Dispatcher` | requests, nesting, results, errors |
| `Mailbox`, `MessageReader`, `MessageWriter`, `Wire`, `Native` | the mailbox through the FFM API (futexes, atomics), messages in place, constants, libc calls |
| `Control` | the control socket: `INTERRUPT` (the running call), `SHUTDOWN`, EOF |
| `Console` | a call's stdout and stderr, for `RESULT` |
| `Options` | integer and decimal types, the time zone |

## Languages

- **`Polyglot`** owns the one polyglot `Context` of the worker (GraalJS and GraalPy; created at the first JavaScript
  or Python routine) and prepares and calls their routines.
  - The `Context` is built with host access to Java objects but not to the worker's classes, class loading,
    reflection, threads, processes or JMX (`lookupAllowed`); a read-only virtual file system serves libraries
    (`LibraryFileSystem`) and packages.
  - **JavaScript** routines get a wrapper function (`JsOracle`): parameters as variables, strict mode, a fresh
    `globalThis` per call, `await` support, `OUT` parameters returned as an array; arguments are converted inside the
    wrapper, so a call is one host-to-guest call.
  - **Python** routines: the body is parsed (`ast`), dedented, and wrapped as a function (with an inner function for
    `OUT` parameters); each call runs the compiled code over a copy of the routine's base globals. A `SyntaxError` of
    the compiled tree is re-raised with its location (GraalPy can't pass it to Java otherwise). At `CREATE`,
    `_check_names` reports names that are never bound.
  - **numpy across languages** (`importPython`, `Mle.importPython`): modules come through a wrapper whose functions
    convert JavaScript and Java arrays (bulk copies in `bulkArray` for numbers); an import hook registers numpy's
    scalar types as numbers for the other languages and makes `numpy.array`/`asarray`/`asanyarray` accept foreign
    arrays. (GraalPy alone gives Python foreign arrays as `polyglot.ForeignList`, which numpy's C code can't read.)
  - Python can't start threads (`threading` is patched to refuse).
- **`JavaRoutines`** compiles Java routines with `javac` in memory: a body becomes a static method of a class (with
  imports of `java.math`, `java.time`, `java.util` and MLE's API; `OUT` parameters as one-element arrays), or the
  source's public class is used. Each routine gets a class loader whose parent sees only the JDK and MLE's API. Java
  libraries are compiled with the routine. Prepared routines are cached per worker by content (schema, source,
  libraries), so a later session of the account reuses loaded classes.
- **`FrameLimit`** (a Truffle instrument on JavaScript's root nodes) refuses JavaScript calls deeper than 1000
  frames, MySQL's limit for JavaScript routines; Python has its own recursion limit.

## The SQL API

- **`com.percona.mle.sql`** is the SQL object model, implemented once in Java: `Session`, `SqlExecute`,
  `PreparedStatement`, `SqlResult` (rows fetched in chunks), `Row`, `Column`, `Warning`, `SqlOptions`, `Schema`,
  `Table`, `Routine`, `Argument`, `Functions` (`mysql.rand`, `uuid`, ...). It talks to the host through `SqlBridge`
  (`SQL` requests).
- **`GuestSql`** exposes it to JavaScript and Python as polyglot proxies; **`JsSql`** and **`PySql`** add each
  language's globals (`session`, `mysql`, `SqlError`), names (snake_case in Python) and value mappings;
  **`JavaSql`** maps values for Java routines (`Mle.session()`).
- `fetch_columns` (Python) reads the remaining rows column by column into numpy arrays filled with one memory copy:
  with the row count known, the arrays are allocated first and each later chunk is copied from the mailbox
  straight into them while it is decoded (`RowSink`), with no `float[]` per `VECTOR`. JavaScript rows get each
  `VECTOR` as a heap `ByteBuffer` that the `Float32Array` shares (one copy).

## Values

- **`Values`** decodes wire values into a neutral Java form (`read`), encodes parameters (`writeParam`) and converts
  a routine's result to the declared SQL type (`writeAs`: ranges, rounding, `SET`/`ENUM` text, dates in the
  session's time zone). Conversion failures are errors of the language `MLE-Type`.
- Per language: `JsOracle.hostForm`/`toJava` (JavaScript routines' arguments and results), `Polyglot.hostForm`/
  `toJava` (Python), and the SQL API mappers for rows.
- `VECTOR` arguments for Python are numpy arrays reused per parameter (read-only, valid during the call).

## Libraries

`LibraryFileSystem` resolves `/schema/name` imports (JavaScript `import`, Python `import_library`, Java
`Mle.importLibrary`, and `USING`) by asking the host (`LIBRARY`, with the hash of the body it has). Bodies are cached
per worker; confirmations per routine and epoch (a new `PREPARE` starts a new epoch), because whether a routine may
import a library depends on the routine (its schema and `USING`). A changed library is used from the next statement.

## Watchers

- **`MemoryWatch`:** native memory (numpy) is freed only when Java's garbage collector collects the Python object
  that refers to it; numpy's `malloc` doesn't make the JVM collect. While a call runs, the watcher samples the
  resident size every 10 ms; when it has grown by a twentieth of the limit since the last time, it causes a
  *young* GC: it allocates short-lived garbage until a weak reference to a fresh object is cleared (~1 ms pause,
  frees the arrays that died young). It never calls `System.gc()`: a forced full GC took 125-190 ms and made every
  ~25th call of a 16 MB `fetch_columns` stream that slow (exp note 45). Counted in `mle_worker_memory_gcs`.
  Freed arrays reach the system at once because workers run with a fixed glibc mmap threshold
  (`mle.malloc_mmap_threshold`, `GLIBC_TUNABLES`): glibc's adaptive threshold rises to 32 MiB after frees, and then
  freed 16 MB arrays stayed in malloc's heap until the worker was killed.
- **`ThreadWatch`:** a routine must not leave threads behind (they would outlive the call, escape `KILL` and serve
  the account's next session). At `RESET`, and at most every 5 seconds after a request, the worker looks for threads
  that aren't the runtime's (by name and by stack: code of a routine, a library or a DBA jar; any live virtual thread;
  a thread started but not yet run); with one, the worker is tainted and not reused.
- **`VectorWarmup`** compiles the vector helper's kernels while a pool worker waits.

## What routines see (`com.percona.mle.api`)

`Mle` (`session()`, `importPython`, `importJs`, `importLibrary`, `context()`), `Vectors`, `VectorSet`, `Matches`
(the vector helper; `VectorEngine` holds its kernels and threads), `MleSqlException`, `MleJson`, `MleSql` (a
low-level SQL client).

## The native build

`worker/native/build-native.sh` builds the worker as a shared library with Native Image, without GraalJS. The same
bootstrap loads it (`--jvm=.../native/libmleworker.so`) through the JNI invocation API. Differences: no JDK, no AOT
cache, only `-D` options pass through (`MLE_JVM_OPTIONS`; runtime options in `MLE_NATIVE_OPTIONS`), the garbage
collector's policy is `Proportionate`; `READY` reports Python only and Java routines are refused. `mle.worker` selects
the build.
