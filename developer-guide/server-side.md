<!-- check-page: skip: a description of the code -->
# The server side

## The language router (server branch)

`component_lang_router` lives in the server tree (`components/lang_router/`), built when `js_lang` is enabled or with
`-DWITH_LANG_ROUTER=ON`. It is what MLE needs from the server.

- **The services it routes:** `external_program_execution` (init, parse, execute, deinit of a routine),
  `external_program_capability_query`, `external_library` and `external_library_ext` (`CREATE LIBRARY`). The server
  has one default implementation of each; the router registers itself as that default in its `init` (a component's
  `PROVIDES` list would be registered too late), and keeps the previous implementations as backends.
- **Choosing a backend:** for a new routine the router reads its language and asks each language component
  (`supports_language`); `lang_router.language_map` settles a language two components claim (`JAVASCRIPT` by `js_lang`
  or by MLE). The handle remembers its backend: later calls go straight to it. Libraries are routed by their
  language the same way.
- **After `init`,** a routine's calls go to its backend through the handle, without a lookup by language.
- **External functions' context:** the branch also keeps an external function's runtime context (`sp_rcontext`)
  across the calls of one statement, instead of building it for every row: about a fifth of the per-call cost.
- **Tests:** the `component_lang_router` suite and `main.sp*`.

## The statement service (server branch)

- **`use_cursor`:** a statement attribute (default true) of the `mysql_stmt_*` services. A prepared SELECT opens a
  server-side cursor, which materialises the whole result into a temporary table and hands rows back from it; with
  `use_cursor = false` execution collects the result like a regular statement, within `buffer_capacity`. MLE turns
  the cursor off: for a 16 MB result it was 13 of 30 ms (exp note 44).
- **Tests:** `test_services.test_execute_prepared_statement` (debug builds: the test component's `no_cursor` debug
  keyword).

## component_mle

### Registration

`component.cc` registers, in `init`: the system variables (`settings.cc`), the status variables (`status.cc`), the
dynamic privileges `CREATE_JAVASCRIPT_ROUTINE`, `CREATE_PYTHON_ROUTINE`, `CREATE_JAVA_ROUTINE`, the UDFs (`udfs.cc`),
a THD slot for the session state, an event-tracking hook (for `DROP USER`/`RENAME USER`: the account's cached workers
are dropped), and it starts the engine. If anything fails, everything registered is undone and `INSTALL` fails.
`deinit` refuses while sessions have MLE state (error 6003, "Cannot uninstall due to connected sessions").

### Services

| Service | Implementation |
|---|---|
| capability query | the languages in `mle.languages` that the installed build runs |
| `init` | `Routine::create`: the routine's description into a `PrepareMsg` |
| `parse` | `Session::parse`: privilege check, `PREPARE` (syntax errors fail `CREATE`), CREATE-time checks as warnings |
| `execute` | `Session::execute` |
| `deinit` | `Session::forget`, `DROP` in the worker |
| `external_library*` | `Libraries::parse_ext`: privilege check, `PARSE_LIBRARY` (syntax only; the server stores the body) |

### Sessions (`session.cc`)

A `Session` per THD (in the THD slot), created at a session's first MLE routine:
- **workers** by principal (`Slot`: the worker and the routines prepared in it), and workers retired during nested
  calls;
- **console:** the session's stdout and stderr, the last call's output, the last error's stack trace and traceback;
- **options** (`mle_set_session_state`) and the time zone sent to workers;
- **`mle.console`:** output and tracebacks as warnings (at most 100 lines per stream and call).

`Hooks` is what the host library calls back during a call: SQL (`SqlRunner`), library requests (the import rule),
liveness and kill checks. `KillGuard` connects the THD's kill handler to the workers of the call.

### SQL from routines (`sql.cc`)

`SqlRunner` runs a call's statements through the server's component services (`mysql_stmt_factory`,
`mysql_stmt_execute`, `mysql_stmt_execute_direct`, `mysql_stmt_bind`, `mysql_stmt_result`, `mysql_stmt_metadata`,
`mysql_stmt_resultset_metadata`, `mysql_stmt_get_*`, `mysql_stmt_diagnostics`, `mysql_stmt_attributes`) in the caller's THD. Results are read row by
row into wire values (`read_row`); parameters bound by type (`bind_value`). An error stays in the caller's
diagnostics area while the routine decides: if it lets the error escape, the client gets it unchanged; if it goes
on, the area is cleared at its next request. A routine's own `SqlError` is raised with a generated `SIGNAL`.

### Settings, status, UDFs

- `settings.cc`: every `mle.*` variable (`reg(...)`: name, flags, comment, default, limits, check and update
  functions). Variables that shape workers restart the engine in their update function, refused while workers are
  in use.
- `status.cc`: `mle_*` status variables, from atomic counters and the engine.
- `udfs.cc`: `mle_session_state`, `mle_session_reset`, `mle_set_session_state`, `mle_eval`, `mle_packages`,
  `mle_packages_refresh` (and `mle_debug_worker_pid` in debug builds), with the `mysqlpp` UDF wrappers.

### Errors

| Code | Name | Use |
|---|---|---|
| 6000 | `ER_LANGUAGE_COMPONENT` | `MLE> ...` (the engine, a lost worker), `MLE-Type> ...` (conversions), warnings |
| 6111 | `ER_LANGUAGE_COMPONENT_INTERNAL` | MLE's own failures; details only in the error log |
| 6112 | `ER_LANGUAGE_COMPONENT_CONCURRENCY_LIMIT` | `mle.max_workers` |
| 6113 | `ER_LANGUAGE_COMPONENT_RUNTIME` | `<Language>> message (where)`: the routine's own errors |
| others | `..._TIMEZONE`, `..._KEYWORD`, `..._UNSUPPORTED_LANGUAGE`, `..._SET_SYSTEM_VARIABLE`, `..._CANNOT_UNINSTALL` | |

The rule: a routine's own error (its code, its SQL) is reported as the routine's (6113 or the SQL error itself); what
MLE itself failed at is 6000 or 6111, never mixed with routine errors, so that applications can tell them apart.
