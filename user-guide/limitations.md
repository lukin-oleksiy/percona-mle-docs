# Limitations

What MLE doesn't do (yet), and the edges to know about. MLE is a proof of concept.

## Platform

- **Linux only, x86-64.** Workers are sandboxed with Landlock (kernel 6.12 or newer) and seccomp; on RHEL-like
  systems Landlock must be enabled on the kernel command line.
- **A per-worker memory limit needs a delegated cgroup** (systemd `Delegate=yes`, or a privileged container).
  Without it, workers have only the Java heap limit.
- **The Python-only native build** runs Python routines only: no JavaScript, no Java, no npm packages or jars.

## Routines

- **Functions can't run SQL** (the server refuses statements in functions and triggers): use a procedure.
- **No state between calls in globals:** every call starts with fresh global variables. Libraries, Python modules and
  Java static fields keep state per worker (for caches); data belongs in tables.
- **Threads:** Python routines can't start threads. Java threads must end with the call: a thread left running
  retires the worker, and `KILL` doesn't stop a routine's own threads (only the call).
- **Values from other languages** are valid during the call that got them.
- **numpy from JavaScript and Java:** module functions take JavaScript and Java arrays, but the methods of a numpy
  array take numpy arrays or Python lists (`a.dot(np.array(b))`).
- **JavaScript is not Node.js:** no `fs`, `process`, `http` or timers (`setTimeout`).
- **Python C extensions** need a GraalPy build (GraalPy's binary wheels, or a build from source).
- **`mle_eval`** can't run SQL and keeps nothing between calls.

## Debugging

- **No step-by-step debugger** (breakpoints, variable inspection) yet: errors carry their location and traceback, and
  output can come back as warnings (see [Debugging routines](../debugging/index.md)).
- **JavaScript has no CREATE-time check for misspelt names** (Python does; Java is compiled).

## Performance

- **A call costs about a microsecond and a half** (a stored SQL function: about one). It doesn't matter for a call
  that does real work; for a trivial function called on millions of rows, SQL is cheaper.
- **Warm-up:** a worker's first calls run before the JIT compiler has optimised them (seconds of CPU for heavy code).
  The pool and the per-account worker cache keep workers warm between sessions.
- **A new JVM worker starts in about 2.5 s**; the pool (`mle.pool_size`) hides it for new sessions.
- **Memory:** a JVM worker that uses Python and numpy needs about 0.6–0.8 GB; the native build about half (see
  [Configuration and sizing](configuration.md)).
- **numpy memory** is freed by Java's garbage collector, which the worker runs itself near `mle.memory_max`. On a host
  whose CPUs are all busy, a loop that allocates large temporary arrays very fast can still pass the limit before
  the collector frees them, and the worker is killed: leave headroom, or reuse arrays (`out=` arguments).
- **Python is single-threaded in a worker** (GraalPy's lock): calls between Python and the other languages are cheap,
  but don't run Python in parallel inside one call.

## Data

- **The largest result** of a statement a routine runs without parameters is `mle.sql_result_max` (64 MB); larger
  results need a prepared statement (read with a cursor) or `fetch_columns`.
- **A routine's result** (with what it printed) and each statement it sends are limited to 64 MB.
- **Python `VECTOR` arguments** are read-only numpy arrays, valid during the call (`copy()` to keep or change one).
