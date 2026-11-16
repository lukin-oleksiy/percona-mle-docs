# Operations

Running MLE day to day: watching it, stopping runaway routines, reading its errors, and what to do when something
goes wrong.

## Watching MLE

Status variables (all named `mle_*`; [Reference](reference.md) lists them):

<!-- output: varies -->
```sql
SELECT VARIABLE_NAME, VARIABLE_VALUE FROM performance_schema.global_status
 WHERE VARIABLE_NAME IN ('mle_status', 'mle_workers', 'mle_pool_idle', 'mle_workers_cached',
                         'mle_workers_spawned', 'mle_worker_failures', 'mle_oom_errors', 'mle_cgroup')
 ORDER BY VARIABLE_NAME;
```

```text
+---------------------+----------------+
| VARIABLE_NAME       | VARIABLE_VALUE |
+---------------------+----------------+
| mle_cgroup          | OFF            |
| mle_oom_errors      | 0              |
| mle_pool_idle       | 2              |
| mle_status          | Active         |
| mle_worker_failures | 0              |
| mle_workers         | 0              |
| mle_workers_cached  | 0              |
| mle_workers_spawned | 2              |
+---------------------+----------------+
```

| Watch | Meaning |
|---|---|
| `mle_workers` against `mle.max_workers` | workers held by sessions now; at the limit, new calls fail (error 6112) |
| `mle_pool_idle` | workers ready for new sessions; 0 for long means sessions wait for new workers (seconds on the JVM build) |
| `mle_workers_spawned` growing fast | workers are not reused: short sessions with the cache full or off, or workers retired (threads left, failures) |
| `mle_worker_failures` | workers lost: crashes in native code, memory limit, killed after `KILL`; the error log says why |
| `mle_oom_errors` | calls that ran out of memory (Java heap or the cgroup limit) |
| `mle_worker_memory_gcs` | collections the worker started itself to stay within `mle.memory_max` (numpy-heavy loops) |
| `mle_cgroup` | `ON` when workers have their own memory limit (`mle.memory_max`) |

The workers are processes of their own (`mle_worker`, children of `mle_launcher`), so the usual OS tools show their
CPU and memory: `top`, `ps`, `systemd-cgtop` with cgroups.

## Stopping a routine

`KILL QUERY`, `KILL` and `MAX_EXECUTION_TIME` stop a routine in any language, as for SQL:

```sql
CREATE FUNCTION spin() RETURNS INT LANGUAGE PYTHON AS $$
    while True:
        pass
$$;
SELECT /*+ MAX_EXECUTION_TIME(500) */ spin();
```

```text
ERROR 3024 (HY000): Query execution was interrupted, maximum statement execution time exceeded
```

A routine stuck in native code that doesn't stop within `mle.interrupt_grace_ms` (1 s) has its worker killed; the
session's next call gets a new one.

## Errors

| Code | Text | Meaning |
|---|---|---|
| 6113 | `<Language>> <exception> (routine, line N, in function)` | the routine's code failed: an exception, a syntax error at `CREATE`; see [Debugging routines](../debugging/index.md) |
| 6000 | `MLE-Type> Cannot convert value ...` | a result or parameter doesn't fit its SQL type |
| 6000 | `MLE> the worker died: <reason>` | the worker was lost during the call (crash, memory limit, killed); the next call gets a new one |
| 6000 | `<Language>> <routine>, line N: ...` (a warning) | a finding of `CREATE`'s checks, or (with `mle.console = WARNINGS`) a line the routine printed |
| 6112 | `Concurrency limit (...) has been reached` | `mle.max_workers` workers are in use |
| 6111 | `Internal error` | an error of MLE itself; the error log has the details |
| the server's | | an SQL statement the routine ran failed, and the routine didn't catch it: the statement's own error |

## The error log

MLE writes to the server's error log when a worker is lost (with the reason: signal, memory limit, exit), when the
engine (re)starts, and when a setting can't take effect (no delegated cgroup, say). Routine errors that reach the
client are not logged.

## Troubleshooting

| Symptom | Check |
|---|---|
| The first call of a session takes seconds | `mle_pool_idle` is 0: raise `mle.pool_size`; on the JVM build a new worker takes about 2.5 s |
| The first Python call of a session is slow | `mle.pool_prewarm` should include `python` (or `numpy`) |
| Calls fail with 6112 | `mle.max_workers`; sessions hold a worker per account until they end or call `mle_session_reset()` |
| `MLE> the worker died` | the error log; for memory, `mle.memory_max` and the routine's data; for crashes, the native library the routine used |
| `mle.memory_max` has no effect | `mle_cgroup` is `OFF`: mysqld needs a delegated cgroup (systemd `Delegate=yes`) |
| New packages are not seen | `SELECT mle_packages_refresh()` after `mle_package install` |
| `SET GLOBAL mle.pool_size` is refused | sessions hold workers; these settings restart the engine |
| `UNINSTALL COMPONENT` is refused | sessions are still connected; disconnect them first |
