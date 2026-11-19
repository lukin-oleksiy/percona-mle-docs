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
- **JavaScript `await`:** a routine whose text contains the word `await`, also in a comment or a string, is
  compiled as an asynchronous function, and such a routine can't be called from the SQL of another routine.
- **JavaScript built-ins are shared by a worker's calls:** every call has fresh global variables, but a change to a
  built-in object (`Array.prototype`, `JSON`) stays for the account's worker.
- **JavaScript `Date` local time** (`getHours()`, `toString()`) is in the server's system time zone, while
  `DATETIME` and `TIMESTAMP` values are converted in the session's: use the UTC methods.
- **Java source is recognised by its text:** whether a routine is a method body or a class, and the class's name,
  are found by patterns, which a comment or a string that looks like a class declaration can mislead.
- **Java libraries are compiled on their own:** a Java library can't use another library.
- **A JavaScript library's name** can't contain `.` or `/`: a routine that imports such a library gets an error.
- **Two Python routines of one name** (in two schemas) used in one session: a traceback of one may show the other's
  source text at the right line number.

## Debugging

- **No step-by-step debugger** (breakpoints, variable inspection) yet: errors carry their location and traceback, and
  output can come back as warnings (see [Debugging routines](../debugging/index.md)).
- **Resetting a connection** (`mysql_reset_connection()`, as connection pools do) does not reset MLE's session
  state: printed output, the last traceback and the options of `mle_set_session_state()` stay. Call
  `mle_session_reset()` after it.
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

## Character sets

- **utf8mb4 only.** Create and call routines from connections whose character set is utf8mb4 (the default of
  MySQL's clients). Other client character sets are not supported in this proof of concept: a routine created from a
  `latin1` connection stores the non-ASCII text of its SQL statements wrongly, whoever calls it later. Tables and
  columns can use any character set: the server converts on the way in and out.
- **Characters outside the Basic Multilingual Plane** (emoji, for example) in a routine's text give the server's
  warning 1300 at `CREATE` and at the first call, and `information_schema.ROUTINES` shows them as `?` (the server
  keeps that copy in utf8mb3; a stored SQL routine does the same). The routine itself runs with the right text. An
  error message loses such characters too; `mle_session_state('traceback')` keeps them.

## Data

- **The largest result** of a statement a routine runs is `mle.sql_result_max` (64 MB); read larger results in
  parts (ranges of a key) or raise the setting.
- **A routine's result** (with what it printed) and each statement it sends are limited to 64 MB.
- **Python `VECTOR` arguments** are read-only numpy arrays, valid during the call (`copy()` to keep or change one).
- **A column named like a method of a row** (`getField`, `toArray`; in Python a list method such as `count`) is
  not an attribute of the row: read it with `row.getField('count')`.
- **The statistics helper computes in `double`:** `BIGINT` and `DECIMAL` values above 2<sup>53</sup> lose their
  last digits there, also as group keys. The vector helper computes distances in `float`.
- **The system time zone** needs a name (`TZ=Europe/Kyiv`, or `/etc/localtime` as a link into the zone database)
  for daylight saving time; without one MLE uses the current offset. `default_time_zone` avoids the question.
