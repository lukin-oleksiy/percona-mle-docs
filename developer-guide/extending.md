<!-- check-page: skip: a description of the code -->
# Extending MLE

Recipes for the common changes. Each ends with tests and docs: an MTR test (or a case in an existing one), the user
guide page that describes the behaviour, and a run of `check_docs.py` when users see the change.

## A system variable

1. `component/settings.cc`: a `reg("name", flags, comment, &var, check, update)` call in `register_settings`, with
   default and limits (`def_val`, `min_val`, `max_val`). Names are `mle.<name>`; `PLUGIN_VAR_READONLY |
   PLUGIN_VAR_NOPERSIST` for paths and anything that must not change at run time.
2. **If it shapes workers** (memory, JVM options, the pool, prewarm): add it to `Settings` (`engine.h`), pass it in
   `start_locked` (`engine.cc`, and the worker's JVM options in `host/defaults.cc` if the worker reads it), and make
   its update function restart the engine (`restart_engine`), refused while workers are in use.
3. **If the worker needs it per call** (like `mle.console`, which only the server reads), keep it on the server side
   when you can: a value read per call costs nothing in the worker.
4. Tests: `basics.test` lists the variables; a test of its effect. Docs: [Reference](../user-guide/reference.md) and
   the page that explains it.

## A UDF

1. `component/udfs.cc`: a class with a constructor (argument checks, result type, nullability) and `calculate`,
   declared with `DECLARE_UDF_INFO_AUTO(name)` in `g_udfs` (the `mysqlpp` wrappers register and unregister them).
2. Throw `std::invalid_argument` for errors the caller should see (`ER_UDF_ERROR` with the message); privilege checks
   with `has_global_privilege`.
3. Session state comes from `Session::current(create)`.
4. Tests: `session_state.test` or a new one; docs: [Reference](../user-guide/reference.md).

## A method of the SQL API

1. Implement it in `com.percona.mle.sql` (`Session`, `SqlResult`, `PreparedStatement`, ...): this is what Java
   routines call directly.
2. Expose it to JavaScript and Python in `GuestSql`: add the name to the class's `NAMES` and a `case` in `call`
   (arguments as polyglot `Value`s; convert results with `lang.list`, `lang.value`). Python gets the snake_case name
   automatically (`PySql.member`).
3. If it needs the server, add an `SqlAction` (or reuse one): `protocol.h`, the codec, `SqlRunner::handle`, and the
   worker's `SqlBridge`.
4. Tests: `sql_js_api.test`, `sql_py.test`, `sql_java.test`. Docs: [SQL from routines](../user-guide/sql.md).

## A data type

1. `component/routine.cc`, `describe()`: the SQL type's wire tag and `ParamType` (name, length, precision, scale).
2. The server side's reading and binding: `Routine::read_args`, `Routine::store_results`, and `read_row` /
   `bind_value` in `sql.cc` for SQL from routines.
3. The codec, if a new tag is needed (`protocol.h`, `codec.cc`, `mle_codec-t`, `Wire.java`, `Values.read`).
4. The worker: `Values.writeAs` (the result in the declared type), and each language's mapping: `JsOracle`
   (`converter`, `hostForm`, `toJava`), `Polyglot` (`converter`, `hostForm`, `toJava`), `JavaRoutines.javaType`, and
   the SQL API mappers (`JsSql.value`, `PySql`, `JavaSql`).
5. Tests: `execute.test` (every type in and out), the SQL API tests. Docs: the type tables of the language pages.

## A language

GraalVM languages (Ruby, R, WebAssembly) fit the polyglot `Context`; another runtime would be a new worker class.

1. **The server:** the language name in `CREATE ... LANGUAGE x` must reach the component: `language_bit` and
   `mle.languages` (`settings.cc`), the capability query, a dynamic privilege `CREATE_<X>_ROUTINE`
   (`component.cc`), the display name for errors (`display_language`).
2. **The worker:** the language's jars (`worker/pom.xml`), `Polyglot.prepare` (wrapping a body with the parameters,
   fresh globals per call), argument and result conversion (`hostForm`, `toJava`), the SQL API's globals for it
   (like `JsSql`/`PySql`), libraries in `LibraryFileSystem`, and `READY`'s language list.
3. **The sandbox:** the language's resources must be readable under Landlock (`host/defaults.cc`), and anything it
   starts (threads, processes) must stay within the rules of [Processes and the sandbox](processes.md).
4. **Tests and docs:** a language page in the user guide, and the matrix on [Languages together](../user-guide/polyglot.md).

## A message of the protocol

See [The wire protocol](protocol.md#changing-the-protocol).

## Conventions

- **Comments say why,** with the measurement or the case behind a decision; code that follows a design note cites it
  (`exp note 39`, `15 §1`). Keep the reasons in this guide when they matter to a change.
- **Errors:** routine errors as the routine's (6113 or the SQL error), MLE's own as 6000/6111 (see
  [The server side](server-side.md#errors)).
- **The worker is untrusted:** validate everything the host decodes; bound what a worker can make mysqld allocate.
- **Behaviour the user sees** goes into the user guide in the same change, with a runnable example.
