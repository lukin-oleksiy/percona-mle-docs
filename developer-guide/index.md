<!-- check-page: skip: a guide to the source code; its commands are for a development host -->
# Developer guide

For people who change MLE or review its code. It explains how the parts fit, why they are built as they are, and how
to build, test, debug and extend them. The [user guide](../user-guide/concepts.md) describes the behaviour; this guide
describes the implementation.

| Page | |
|---|---|
| this page | the parts, the code map, the main design decisions |
| [Building](building.md) | prerequisites, CMake options, the worker's Maven build, the native build, packages, Docker |
| [Testing](testing.md) | the MTR suite, unit tests, the harness, the docs checker, benchmarks |
| [A call, end to end](call-path.md) | from `CREATE FUNCTION` and `SELECT f()` through the router, the component, the mailbox and the worker, and back; SQL from routines; errors; `KILL` |
| [Processes and the sandbox](processes.md) | the launcher, workers, cgroups, Landlock, seccomp, the pool and the per-account cache, the threat model |
| [The wire protocol](protocol.md) | the shared-memory mailbox, messages, values, limits |
| [The worker](worker.md) | the Java side: dispatcher, languages, SQL API, values, libraries, watchers, the native build |
| [The server side](server-side.md) | the language router and `component_mle`: services, sessions, SQL execution, settings |
| [Extending MLE](extending.md) | adding a setting, a UDF, an SQL API method, a type, a language |
| [Diagnostics and profiling](diagnostics.md) | worker output, JFR, Truffle logs, perf, the benchmark method |

## The parts

```text
 mysqld                                                   worker processes (one per session and account)
 ┌────────────────────────────────────────────┐           ┌──────────────────────────────────────────┐
 │ stored programs (SQL layer)                │           │ bootstrap (C++): seccomp, JNI            │
 │   │  external_program_* services            │  mailbox  │ JVM: Dispatcher                          │
 │   ▼                                         │  (memfd,  │   ├─ JavaRoutines (javac, class loaders) │
 │ component_lang_router  (server branch)     │  futex)   │   ├─ Polyglot: one Context, GraalJS +    │
 │   │  routes by language                    │◀────────▶│   │   GraalPy (+ numpy)                  │
 │   ▼                                         │  control  │   ├─ SQL API core (com.percona.mle.sql) │
 │ component_mle (percona-mle)                │  socket   │   └─ values, libraries, watchers         │
 │   sessions · routines · SqlRunner · UDFs   │           └──────────────────────────────────────────┘
 │   libmle_host: codec, worker & launcher    │                    ▲ clone3, cgroup leaf, Landlock
 │   clients                                  │──── socket ───▶ mle_launcher (one per engine)
 └────────────────────────────────────────────┘
```

- **`component_lang_router`** (in the server; built with `js_lang` or `-DWITH_LANG_ROUTER=ON`) implements the
  server's external-program services and sends each routine to the component of its language. It lets MLE and
  `js_lang` (V8) run side by side.
- **`component_mle`** (this repository's `component/`) implements those services for `JAVASCRIPT`, `PYTHON` and
  `JAVA`: it keeps per-session state, prepares and calls routines in workers, runs their SQL, and provides the
  `mle.*` variables, `mle_*` status variables and UDFs.
- **The host library** (`host/`, `include/mle/`) is the mysqld side of the protocol: the codec, the worker client
  (the call loop), the launcher client. It has no MySQL dependencies; the unit tests and the harness use it without
  mysqld.
- **`mle_launcher`** (`launcher/`) starts and sandboxes workers on request. mysqld never forks.
- **The worker** (`worker/`): a small C++ bootstrap (`mle_worker`) that installs seccomp and starts a JVM (or loads
  the native image) through JNI, and the Java worker (`mle-worker.jar`): GraalJS, GraalPy, Java routines and the SQL
  API.

## Code map

| Directory | What |
|---|---|
| `component/` | `component.cc` (registration, services), `session.cc` (per-session state, prepare, call, report), `routine.cc` (a routine's description and arguments), `sql.cc` (SqlRunner: SQL from routines), `engine.cc` (launcher, pool, per-account cache), `settings.cc`, `status.cc`, `udfs.cc` |
| `include/mle/` | `protocol.h` (the protocol, the single definition), `codec.h`, `worker.h`, `launcher.h`, `launcher_protocol.h`, `value.h`, `defaults.h` |
| `host/` | `codec.cc`, `worker.cc` (the call loop, KILL, waiting), `launcher.cc`, `defaults.cc` (JVM options, worker classes) |
| `launcher/` | `main.cc` (the poll loop, spawn, pool), `child.cc` (between clone and exec: Landlock), `cgroup.cc` |
| `worker/bootstrap/` | `main.cc` (JNI start), `seccomp.cc` |
| `worker/src/main/java/com/percona/mle/worker` | the worker: `Main`, `Dispatcher`, `Polyglot`, `JsOracle`, `JavaRoutines`, `GuestSql`, `JsSql`, `PySql`, `JavaSql`, `Values`, `LibraryFileSystem`, `Mailbox`, `Control`, watchers |
| `.../api`, `.../sql` | what routines see: `Mle`, `Vectors`, `MleSqlException`; the SQL object model (`Session`, `SqlResult`, `Row`, ...) |
| `worker/native/` | the Python-only native build (`build-native.sh`) |
| `worker/python/` | the numpy virtual environment (GraalPy Maven plugin) |
| `tools/` | `mle_package` (the DBA's package tool), `mle_aot_cache` (the JDK AOT cache at install) |
| `harness/` | `mle_harness`: benchmarks of the host library and workers without mysqld |
| `unittest/` | GoogleTest binaries (`mle_*-t`) and `mle_test_worker` |
| `mysql-test/suite/component_mle/` | the MTR suite |
| `cmake/`, `packaging/`, `docker/` | building against a server tree, packages (deb, rpm, SELinux), images |

## Design decisions

The main decisions, with the reasons that still hold. Code comments refer to the design notes they come from (for
example `exp note 39`, `15 §1`, `D-8`); those notes are not published, so the reasons are summarised here.

| Decision | Why |
|---|---|
| **Out of process.** Routines never run in mysqld. | numpy and other native code can read process memory and crash the process; GraalVM's in-process sandbox can't contain native extensions. A process boundary also gives per-worker memory limits and makes a crash cost one worker. |
| **A worker per session and account (principal).** | Code of one account must never see another account's data. The account is the routine's definer, or the caller for `SQL SECURITY INVOKER`. |
| **A launcher.** mysqld asks a small single-threaded process to spawn workers. | Forking a large multi-threaded mysqld is unsafe and slow; the launcher owns cgroups and the pool, and needs no privileges mysqld lacks. |
| **One JVM for three languages.** Java on HotSpot; GraalJS and GraalPy in one polyglot `Context`. | Calls between languages are in-process function calls; one SQL API, one value conversion, one memory limit. Java routines run on the real JDK (Vector API, threads, javac). |
| **Fresh globals per call.** | Calls must not leak state through globals (MySQL's JavaScript semantics); state that should persist goes into libraries, per worker. |
| **SQL through the server's statement services**, in the caller's session. | The same transaction, locks and privileges as an SQL routine; replication works unchanged; no second connection. |
| **The worker is untrusted.** | Everything read from shared memory is copied before it is validated; a protocol violation ends the worker. |
| **Workers are reused only for the same account**, after a reset. | JIT-compiled code stays warm for the account's next session without crossing the account boundary. |
| **A Python-only native build.** | Python users get half the memory per worker and ten times faster start; it can't host Java routines or JavaScript. |
| **Errors carry their location; the trace is kept per session.** | Users need to know where code failed without a second query. |
