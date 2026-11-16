<!-- check-page: skip: test commands for a development host -->
# Testing

Four kinds of tests, from fast to slow: unit tests (no mysqld), the MTR suite (mysqld with MLE), the docs checker
(the Docker image), benchmarks. Run the first two for every change; the docs checker when behaviour users see
changes; benchmarks when a change touches a call path.

## Unit tests (GoogleTest)

```sh
systemd-run --user --scope -q -p Delegate=yes BUILD-mle/runtime_output_directory/mle_codec-t
```

`systemd-run ... Delegate=yes` gives the test a cgroup it may manage, as mysqld has in production; without it the
cgroup tests skip.

| Binary | Tests |
|---|---|
| `mle_codec-t` | the codec: every message and value, limits |
| `mle_codec_fuzz-t` | the decoders on corrupted and random input (seeds from real messages) |
| `mle_launcher-t` | the launcher and the host library with `mle_test_worker` (a C++ worker, no GraalVM): spawn, pool, cgroups, Landlock |
| `mle_worker_protocol-t` | the host against a misbehaving test worker: bad messages, timeouts, death |
| `mle_java_worker-t` | Java routines in a real worker under the full lockdown |
| `mle_polyglot_worker-t` | JavaScript, Python and Java in one worker: SQL, numpy, KILL |
| `mle_worker_failures-t` | escapes and failures: files, network, processes, memory, crashes |
| `mle_body_forms-t` | `CREATE ... AS $$ body $$` forms in each language |

## The MTR suite

```sh
BUILD-mle/mtr --parallel=4 [--force]          # the whole suite (about ten minutes)
BUILD-mle/mtr debugging polyglot_numpy        # tests by name, without the suite prefix
BUILD-mle/mtr --record new_test               # write r/new_test.result; review it before committing
```

`BUILD-mle/mtr` is generated (`cmake/mtr.sh.in`): it runs the server build's `mysql-test-run.pl` with the suite
directory and the component's plugin directory. Tests start with `--source ../include/have_mle_component.inc`, which
skips them unless they run through that wrapper (the server's own MTR run doesn't set it up), and
`include/have_mle_native.inc` for tests of the native build.

| Test | Covers |
|---|---|
| `basics` | INSTALL, the engine, variables, the three languages at CREATE, UNINSTALL refused with sessions |
| `execute` | arguments and results of every SQL type, OUT/INOUT, errors, a worker per account |
| `debugging` | error locations, tracebacks, `last_output`, `mle.console`, `mle_eval`, CREATE-time checks |
| `failures` | KILL, `MAX_EXECUTION_TIME`, lost workers and launcher, heap exhaustion |
| `install_errors` | INSTALL fails clearly when the engine can't start |
| `session_state` | `mle_session_state`, `mle_session_reset`, `mle_set_session_state` |
| `sql_core`, `sql_js`, `sql_js_api`, `sql_py`, `sql_java` | SQL from routines in each language |
| `sql_scenarios` | triggers, nesting across languages, recursion limits, transactions |
| `sql_rpl` | replication of routines' statements |
| `libraries`, `libraries_multi` | `CREATE LIBRARY` in three languages, `USING`, across languages, `Mle.importLibrary` |
| `polyglot_reach`, `polyglot_numpy`, `true_mle` | languages calling each other: jars, numpy, npm, libraries |
| `python_semantics`, `numpy_memory`, `native_python` | Python: fresh globals, no threads, numpy memory, the native build |
| `java_threads` | parallel work in Java routines, threads left behind |
| `vectors` | the vector helper |
| `packages` | `mle_package` packages from every language |
| `worker_reuse` | the per-account worker cache |
| `oracle_examples` | the JavaScript examples of MySQL's manual, with the manual's results |
| `router`, `js_compare` | MLE beside `js_lang` behind the router |
| `review_fixes`, `review2_fixes` | regressions found by reviews |

**Writing a test:**
- Python bodies need their indentation, which `mysqltest` strips from SQL: create Python routines through a file
  run by the `mysql` client (`--write_file`, then `--exec $MYSQL test < file`), as the existing tests do.
- Workers belong to connections: run routines in a `--connect`ed connection, `--disconnect` it, and
  `--source include/wait_until_count_sessions.inc` before `UNINSTALL COMPONENT` (it is refused while sessions
  exist).
- Results must not depend on timing: mask pids, durations and death reasons (`--replace_regex`).

**Server tests** for the router live in the server branch: `main.sp*` and the `component_lang_router` suite.

## The harness

```sh
BUILD-mle/runtime_output_directory/mle_harness --dir <mle dir> --java-home <jdk> [--no-aot] [--cgroup] startup|calls|memory|all
```

Benchmarks of the host library and workers without mysqld: worker start (with and without the AOT cache), the
per-call cost of the mailbox, memory per worker.

## The docs checker

`check_docs.py` in the docs repository runs every command on every page of the documentation against the Docker
image and compares the outputs shown on the page. After a change users can see, rebuild the images and run it; with
`--record` it writes the actual outputs into the pages (review the diff).

## Benchmarks

- **Method:** release builds, the CPU governor set to `performance`, a quiet machine (load below 2), at least two
  runs; compare against the previous runs' spread, not a single number.
- **Steady-state per-call cost:** a function called for 1M rows, minus a plain scan of the same rows.
- **Worker start:** a new connection's first call, with a worker from the pool and without.
- **Memory:** a worker's PSS with several sessions holding workers at once.
- The benchmark scripts behind the published figures cover these, for the JVM and the native build, side by side
  with SQL stored functions.
