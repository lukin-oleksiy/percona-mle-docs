<!-- check-page: skip: commands for a development host -->
# Diagnostics and profiling

For finding out what a worker does. None of this is for production: the variables loosen the sandbox or slow
workers down.

## Environment variables of mysqld

| Variable | Effect |
|---|---|
| `MLE_WORKER_STDERR=1` | workers write to mysqld's stderr (the error log): JVM messages, crashes, debug prints |
| `MLE_JVM_OPTIONS="..."` | more JVM options for JVM workers (`-D` options also reach native workers) |
| `MLE_NATIVE_OPTIONS="..."` | runtime options for native workers, e.g. `-XX:+PrintGC` |
| `MLE_DEBUG_WRITE_DIR=/dir` | workers may write in `/dir` (Landlock allows it; a warning in the error log); an absolute, writable path |

## Seeing a worker

- **The error log** says why a worker was lost (signal, exit status, the memory limit) and when the engine
  restarted.
- **`mle_debug_worker_pid()`** (debug builds) returns the current principal's worker pid, for `gdb -p`, `perf -p` or
  `/proc`.
- **Status variables** (`mle_workers`, `mle_workers_spawned`, `mle_worker_failures`, `mle_worker_memory_gcs`) show
  reuse and failures over time.
- **Inside a routine:** `mle_session_state('traceback')`, `'stdout'`, `'stderr'`, and `mle.console = WARNINGS` (the
  user guide's [Debugging routines](../debugging/index.md)).

## JDK Flight Recorder (JVM workers)

```sh
MLE_DEBUG_WRITE_DIR=/tmp/mle-jfr \
MLE_JVM_OPTIONS="-XX:FlightRecorderOptions=repository=/tmp/mle-jfr -XX:StartFlightRecording=delay=10s,duration=30s,filename=/tmp/mle-jfr/w.jfr,settings=profile" \
  mysqld ...
jfr view hot-methods /tmp/mle-jfr/w.jfr
jfr print --events jdk.GarbageCollection /tmp/mle-jfr/w.jfr
```

Every worker started with these options records; start a workload after the delay.

## Truffle (JavaScript and Python)

The same write directory takes Truffle's logs:

```sh
MLE_JVM_OPTIONS="-Dpolyglot.log.file=/tmp/mle-jfr/truffle.log -Dpolyglot.engine.TraceCompilation=true"
```

`TraceCompilation` shows which guest functions are compiled and when; deoptimisation loops show up as the same
function compiled again and again.

## perf (native workers)

The native image has no symbols by default. Build it with `MLE_NATIVE_IMAGE_OPTIONS=-g` (read by
`worker/native/build-native.sh`), keep its `.debug` file beside `libmleworker.so`, and:

```sh
perf record -g -p <worker pid> -- sleep 10
perf report
```

For JVM workers, JFR is the better tool: perf sees JIT-compiled code without names unless the JVM writes a perf map
(`MLE_JVM_OPTIONS=-XX:+DumpPerfMapAtExit` with `MLE_DEBUG_WRITE_DIR=/tmp`: the map is `/tmp/perf-<pid>.map`).

## Common investigations

| Question | Where to look |
|---|---|
| A call is slower than expected | JFR hot methods; `TraceCompilation` for guest code that never gets compiled; the per-call benchmark against an SQL function |
| Memory grows | `mle_worker_memory_gcs`, JFR's GC events, the worker's PSS (`/proc/<pid>/smaps_rollup`); numpy memory is freed only by Java GCs |
| A worker dies | the error log (signal, OOM kill); `MLE_WORKER_STDERR=1` for the JVM's own messages; its crash report needs `-XX:ErrorFile=<write dir>/hs_err_%p.log` |
| Workers are not reused | `mle_workers_spawned` vs `mle_worker_reuses`; threads left by Java code (`ThreadWatch`); `mle.worker_cache_size` |
| A protocol error (6111) | the error log has the host's reason; reproduce with the unit tests (`mle_worker_protocol-t`) |
