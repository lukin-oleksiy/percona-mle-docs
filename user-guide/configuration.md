# Configuration and sizing

MLE's settings are system variables named `mle.*`; [Reference](reference.md) lists them all. This page explains
the ones a DBA sets, and how to size a server for MLE.

```sql
SELECT VARIABLE_NAME, VARIABLE_VALUE FROM performance_schema.global_variables
 WHERE VARIABLE_NAME IN ('mle.worker', 'mle.languages', 'mle.memory_max', 'mle.pool_size', 'mle.max_workers')
 ORDER BY VARIABLE_NAME;
```

```text
+-----------------+------------------------+
| VARIABLE_NAME   | VARIABLE_VALUE         |
+-----------------+------------------------+
| mle.languages   | JAVASCRIPT,PYTHON,JAVA |
| mle.max_workers | 64                     |
| mle.memory_max  | 2147483648             |
| mle.pool_size   | 2                      |
| mle.worker      | AUTO                   |
+-----------------+------------------------+
```

## How memory is used

Each worker is a process of its own: one per session and account that calls routines, plus the idle workers MLE keeps
ready. A worker's memory depends on the build and on what its routines load:

| | JVM build | Python-only native build |
|---|---|---|
| an idle worker in the pool, Python started (`pool_prewarm = python`) | about 330 MB | about 350 MB |
| a worker after its first Python call | about 600 MB | about 360 MB |
| after `import numpy` | about 770 MB | about 500 MB |
| each of eight workers that use numpy (proportional share, PSS) | about 700 MB | about 300 MB |
| a new worker, until its first call returns | about 2.5 s | about 0.25 s |
| a session's first call, with a worker from the pool | about 10–15 ms | about 10 ms |

(Measured on an 8-core x86-64 laptop with Linux; the figures are resident memory unless noted. Routines that hold
large data need more.)

Peak memory is roughly:

> (sessions calling routines at the same time × accounts per session) × memory per worker
> \+ `mle.pool_size` × an idle worker + `mle.worker_cache_size` × an idle worker

## Limits

| Variable | Default | What it limits |
|---|---|---|
| `mle.memory_max` | 2 GB | each worker's memory (JVM, JavaScript, Python, numpy): a cgroup limit |
| `mle.heap_max` | 512 MB | each worker's Java heap |
| `mle.max_workers` | 64 | workers held by sessions at a time; beyond it calls fail (error 6112) |
| `mle.languages` | `JAVASCRIPT,PYTHON,JAVA` | the languages routines may use |

- numpy's memory is freed by Java's garbage collector; near the limit the worker runs it itself
  (`mle_worker_memory_gcs`). On a host whose CPUs are all busy, a loop that allocates large temporary arrays very
  fast can still pass the limit: leave headroom, or reuse arrays (`out=` arguments).
- `mle.memory_max` needs a delegated cgroup (systemd `Delegate=yes` for mysqld's service, or a privileged container):
  without one, MLE logs a warning and workers have only the Java heap limit. `SHOW STATUS LIKE 'mle_cgroup'` says
  which is in effect.
- A routine that runs out of memory fails its call: error 6113 (`JavaScript> Java heap space`, say) when the Java
  heap is full, error 6000 (`MLE> the worker died: ...`) when the worker goes over `mle.memory_max`. The session's
  next call gets a new worker if it needs one.

## Start-up: the pool and the worker cache

| Variable | Default | What |
|---|---|---|
| `mle.pool_size` | 2 | workers started in advance, ready for new sessions |
| `mle.pool_prewarm` | `vectors` | what pool workers load while they wait: `none`, or a list of `python`, `numpy`, `vectors` |
| `mle.worker_cache_size` | 4 | idle workers kept after a session ends, for the same account's next session |
| `mle.worker_cache_timeout` | 60 | seconds a kept worker waits |

- **The pool** hides the worker's start (2.5 s on the JVM build): a session's first call takes a waiting worker.
- **`pool_prewarm`:** `python` starts Python in pool workers (a session's first Python call is then fast, at about
  200 MB more per idle worker), `numpy` also imports numpy, `vectors` prepares the vector helper.
- **The worker cache** keeps a worker for its account: the next session of the account gets code that is already
  compiled and fast. A worker is never given to another account.

These variables restart MLE's engine when changed; that is refused while sessions hold workers (as root):

<!-- as: root -->
```sql
SET GLOBAL mle.pool_size = 4;
SELECT @@global.mle.pool_size;
SET GLOBAL mle.pool_size = DEFAULT;
```

```text
+------------------------+
| @@global.mle.pool_size |
+------------------------+
|                      4 |
+------------------------+
```

## Choosing the build

`mle.worker` (read-only; set it in the configuration file) selects the build when both are installed:

- `AUTO` (default): the JVM build when it is installed, else the native one.
- `JVM`: JavaScript, Python and Java, and every language calling the others.
- `NATIVE`: Python only, with about half the memory per worker and ten times faster worker start.

See [Install](../install/index.md) for the packages.

## Other settings

| Variable | Default | What |
|---|---|---|
| `mle.console` | `OFF` | per session: `WARNINGS` adds what routines print to the statement's warnings (see [Debugging routines](../debugging/index.md)) |
| `mle.vector_threads` | 4 | threads the vector helper may use in one call |
| `mle.interrupt_grace_ms` | 1000 | how long a killed routine has to stop before its worker is killed |
| `mle.sql_result_max`, `mle.sql_statements_max` | 64 MB, 1024 | SQL from routines (see [SQL from routines](sql.md#limits)) |
| `mle.package_dir` | `<mle.dir>/packages` | where `mle_package` installs third-party packages (read-only) |
