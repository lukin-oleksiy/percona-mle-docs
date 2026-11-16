# Reference

## System variables

Variables marked "restarts" restart MLE's engine when changed (new pool, new workers); that is refused while
sessions hold workers. Read-only ones are set in the configuration file (`loose-mle.dir = ...`).

| Variable | Default | Kind | What |
|---|---|---|---|
| `mle.worker` | `AUTO` | read-only | the build that runs routines: `JVM`, `NATIVE` (Python only), `AUTO` (JVM if installed) |
| `mle.languages` | `JAVASCRIPT,PYTHON,JAVA` | dynamic | the languages MLE runs |
| `mle.memory_max` | 2 GB | dynamic, restarts | each worker's memory limit (cgroup `memory.max`); needs a delegated cgroup |
| `mle.heap_max` | 512 MB | dynamic, restarts | each worker's Java heap |
| `mle.pool_size` | 2 | dynamic, restarts | workers started in advance for new sessions |
| `mle.pool_prewarm` | `vectors` | dynamic, restarts | what pool workers load while they wait: `none`, or a list of `python`, `numpy`, `vectors` |
| `mle.vector_threads` | 4 | dynamic, restarts | threads the vector helper may use in one call (at most the number of CPUs) |
| `mle.max_workers` | 64 | dynamic | workers held by sessions at a time (one per session and account) |
| `mle.worker_cache_size` | 4 | dynamic | idle workers kept for the same account's later sessions; 0: none |
| `mle.worker_cache_timeout` | 60 | dynamic | seconds a kept worker waits |
| `mle.interrupt_grace_ms` | 1000 | dynamic | how long a killed routine has to stop before its worker is killed |
| `mle.spin_us` | 50 | dynamic | microseconds a session spins waiting for its worker before it sleeps |
| `mle.console` | `OFF` | dynamic, session | `WARNINGS`: what calls print, and a failed call's traceback, become the statement's warnings |
| `mle.sql_result_max` | 64 MB | dynamic | the largest result of a statement without parameters that a routine runs |
| `mle.sql_row_max` | 16 MB | dynamic | the largest row of a prepared statement that a routine runs |
| `mle.sql_statements_max` | 1024 | dynamic | statements a call keeps open |
| `mle.sql_chunk_rows` | 1000 | dynamic | rows sent to the worker at a time (a chunk also ends at about 768 KB) |
| `mle.cgroup_base` | `AUTO` | read-only | where worker cgroups go: `AUTO` (beside mysqld's, if delegated), `NONE`, or a path |
| `mle.dir` | `<plugin_dir>/mle` | read-only | MLE's files (launcher, worker, runtimes) |
| `mle.java_home` | `<mle.dir>/jdk` | read-only | the GraalVM JDK that runs JVM workers |
| `mle.package_dir` | `<mle.dir>/packages` | read-only | third-party packages installed with `mle_package` |

## Status variables

| Variable | What |
|---|---|
| `mle_status` | `Active` when MLE runs routines |
| `mle_languages_supported` | the languages MLE runs now |
| `mle_cgroup` | `ON` if workers run in cgroups of their own (`mle.memory_max` applies) |
| `mle_workers` | workers held by sessions |
| `mle_pool_idle` | started workers waiting in the pool |
| `mle_workers_cached`, `mle_worker_reuses` | workers kept for their account's next session; times one was reused |
| `mle_workers_spawned` | workers started |
| `mle_worker_failures` | workers lost (crash, memory limit, killed after `KILL`, protocol errors) |
| `mle_oom_errors` | calls that ran out of memory |
| `mle_worker_memory_gcs` | collections workers started to stay within `mle.memory_max` |
| `mle_memory_used` | Java heap in use by session workers, % of their limits |
| `mle_heap_status` | `Allocated` while workers exist, else `Not Allocated` |
| `mle_sessions`, `mle_sessions_max` | sessions with MLE state now, and at most |
| `mle_session_resets` | `mle_session_reset()` calls |
| `mle_threads`, `mle_threads_max` | routine calls running now, and at most |
| `mle_stored_functions`, `mle_stored_procedures`, `mle_stored_programs` | MLE routines in sessions' caches |
| `mle_stored_program_bytes_max` | the largest routine body seen |
| `mle_stored_program_sql_max` | always 0 (not tracked) |

## Functions (UDFs)

| Function | What |
|---|---|
| `mle_session_state(key)` | the session's MLE state: `stdout`, `stderr`, `last_output`, `traceback`, `stack_trace`, `is_active`, `stored_functions`, `stored_procedures`, `stored_programs` |
| `mle_session_reset([what])` | without argument: release the session's workers and forget its routines, output and options; with `'stdout'`, `'stderr'` or `'output'`: clear that output only |
| `mle_set_session_state(json)` | JavaScript value options for the session: `{"integer_type": ..., "decimal_type": ...}` (before routines are loaded) |
| `mle_eval(language, code)` | run a snippet; returns what it printed and the value of its last expression (see [Debugging routines](../debugging/index.md#trying-code-mle_eval)) |
| `mle_packages()` | the installed third-party packages (JSON) |
| `mle_packages_refresh()` | new workers see newly installed packages (`SYSTEM_VARIABLES_ADMIN`) |

## Privileges

| Privilege | For |
|---|---|
| `CREATE_JAVASCRIPT_ROUTINE`, `CREATE_PYTHON_ROUTINE`, `CREATE_JAVA_ROUTINE` | creating routines and libraries in that language (with `CREATE ROUTINE`), and `mle_eval` |
| `EXECUTE` | calling a routine |
| `SYSTEM_VARIABLES_ADMIN` | `mle.*` variables, `mle_packages_refresh()` |

## Statements

| Statement | |
|---|---|
| `CREATE FUNCTION ... LANGUAGE {JAVASCRIPT \| PYTHON \| JAVA} [USING (lib [AS alias], ...)] AS $$ ... $$` | a routine |
| `CREATE PROCEDURE ...` | the same, with `IN`, `OUT`, `INOUT` parameters |
| `CREATE LIBRARY [IF NOT EXISTS] name LANGUAGE {JAVASCRIPT \| PYTHON \| JAVA} AS $$ ... $$` | a library |
| `DROP LIBRARY [IF EXISTS] name` | |
| `information_schema.ROUTINES` (`EXTERNAL_LANGUAGE`), `LIBRARIES`, `ROUTINE_LIBRARIES` | what exists |

## Error codes

See [Operations](operations.md#errors).
