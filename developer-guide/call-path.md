<!-- check-page: skip: a description of the code -->
# A call, end to end

What happens for `CREATE FUNCTION f(...) LANGUAGE PYTHON AS $$ ... $$` and then `SELECT f(1)`: through the server, the
router, `component_mle`, the host library, the mailbox and the worker, with SQL coming back, and how errors and `KILL`
travel. Function names refer to `component/`, `host/` and the worker's Java classes.

```text
 SELECT f(1)
   │ stored program runtime (sp_head, Item_func_sp)
   ▼
 component_lang_router ── init/parse/execute by the routine's language ──▶ component_mle
   │                                                                       │ Routine::create, Session::parse
   │                                                                       │ Session::execute
   ▼                                                                       ▼
                                                     Worker::call (host/worker.cc)
                                                       │ PREPARE (once per worker and routine)
                                                       │ CALL ──────────────▶ Dispatcher.call (worker)
                                                       │                        │ Polyglot / JavaRoutines
                                                       │ ◀──── SQL ─────────────┤ session.run_sql(...)
                                                       │ SqlRunner (sql.cc)     │
                                                       │ ───── SQL_RESULT ────▶ │
                                                       │ ◀──── RESULT ──────────┘
                                                     Session::report, Routine::store_results
```

## CREATE: the router and the services

- The server calls the **external program services** (`external_program_execution`,
  `external_program_capability_query`, `external_library`, `external_library_ext`) for routines whose language isn't
  SQL. `component_lang_router` registers itself as their default implementation (by hand, in `init`: a component's
  `PROVIDES` would register too late).
- For a new routine the router asks each language component's capability query (`supports_language`), picks one
  (`lang_router.language_map` decides when two claim a language), and remembers the routine handle's backend:
  `parse`, `execute` and `deinit` then go straight to it.
- In `component_mle`, **`init` → `Routine::create`** reads the routine's description through the stored-program
  services: the language, function or procedure, the parameters (names checked against reserved words), the result
  type, the body, the schema and the `USING` libraries. Each SQL type maps to a wire tag (`describe()`,
  `routine.cc`). The result is a `PrepareMsg`.
- **`parse` → `Session::parse`** checks the language's privilege (`CREATE_<LANG>_ROUTINE`) and prepares the routine in
  the session's worker, so syntax errors fail `CREATE`. With `check` set (CREATE only), the worker also reports names
  that are never defined (Python); the server turns those lines into warnings.

## The call

1. **`Session::execute`** reads the arguments (`Routine::read_args`, the `mysql_stored_program_runtime_argument_*`
   services; `OUT` parameters go as `NULL`).
2. **`worker_for`** finds the session's worker for the routine's principal (`'user'@'host'` of the definer, or of
   the invoker for `SQL SECURITY INVOKER`): an existing one, one from the account's cache, or a new one from the
   launcher's pool. A new worker gets the session's options and time zone first (`OPTIONS`).
3. **`prepare_in`** sends `PREPARE` once per worker and routine id. The worker compiles the body: a JavaScript
   wrapper function (`JsOracle`), a Python function built from the body's AST (`_load_body`), or a Java class compiled
   with `javac` in memory (`JavaRoutines`, cached per worker by content).
4. **`Worker::call`** (`host/worker.cc`) writes `CALL` (routine id and arguments) into the mailbox and waits: it spins,
   then sleeps on a futex, and on each tick checks the worker's liveness and the session's `KILL` state.
5. **The worker** (`Dispatcher.call`) decodes the arguments, converts them to the language's values, calls the
   routine, converts the result and `OUT` values to the declared SQL types (`Values.writeAs`), and answers `RESULT`
   with what the call printed and, on failure, the error and its stack frames.
6. **`Session::report`** stores the result (`Routine::store_results`: the `return_value_*` and
   `runtime_argument_*` services), keeps the output for `mle_session_state`, and turns errors into MySQL errors.

## SQL from a routine

While the host waits for a `RESULT`, the worker may send `SQL` instead (the routine called `session.run_sql`):

- **`SqlRunner`** (`sql.cc`) runs it through the server's statement services in the caller's session
  (`mysql_stmt_execute_direct` for text without parameters, buffered whole up to `mle.sql_result_max`; prepare,
  bind and execute with a cursor otherwise, rows up to `mle.sql_row_max`). Statement handles use utf8mb4;
  `passResultToClient` sends a result set to the client through the session's protocol.
- **Rows go back in chunks** (`mle.sql_chunk_rows`, about 768 KB), so a large result doesn't need to fit in the
  mailbox.
- **Functions and triggers:** the server refuses statements there; its check for direct statements misses a function
  called by a trigger, so the runner asks the prepared path once per call (`refused_here`) and rewords the refusal
  for routine authors (`explain_refusal`).
- **Nesting:** an SQL statement may call another routine (of any language). The host then sends `CALL` (or
  `PREPARE`) to the same worker at depth + 1, and the worker handles it recursively while the outer call waits
  (up to 32 levels).
- **Libraries:** a routine that imports a database library sends `LIBRARY` (schema, name, the hash it knows); the
  host checks that the routine may import it (its own schema, or its `USING` list), reads the body with the
  `mysql_library` service and answers with the body, "unchanged", or a refusal.

## Errors

`Session::report` maps a failed call:

| Worker status | Client sees |
|---|---|
| an SQL error the routine didn't catch, still in the diagnostics area | that error, unchanged (`propagates`) |
| an `SqlError` the routine threw | the same error, raised with a generated `SIGNAL` (`raise`); in a function, where `SIGNAL` is refused, a routine error |
| a guest exception | 6113 `<Language>> message (routine, line N, in function)`; the frames are kept for `mle_session_state('traceback')` |
| a value that doesn't fit its SQL type | 6000 `MLE-Type> ...` |
| interrupted | the server's KILL error (`ER_QUERY_INTERRUPTED`, or the server's own report) |
| the worker died or was killed | 6000 `MLE> the worker died: <why>`; the worker is dropped |
| a protocol violation | 6111 (internal error), the details in the error log; the worker is dropped |

## KILL

- During a call, `KillGuard` installs a THD kill handler. `KILL` (or `MAX_EXECUTION_TIME`) makes it send `INTERRUPT`
  (with the call's sequence number) on the control socket of every worker on the session's nesting stack.
- The worker's `Control` thread interrupts the running code: `Context.interrupt` for JavaScript and Python,
  `Thread.interrupt` for Java, escalating to the other if needed. An `INTERRUPT` that arrives before the call starts
  is kept.
- The host's wait loop also polls the session's kill state; if the worker hasn't answered after
  `mle.interrupt_grace_ms`, it is killed (`SIGKILL`) and the call fails.
- A killed call reports the kill even if the routine happened to finish.
