# Concepts

MLE runs stored functions and procedures written in JavaScript, Python and Java in Percona Server. This page
explains the parts and the rules that hold in all three languages; the language pages build on it.

## Routines

A routine is created like an SQL stored routine, with a language and a body in place of SQL statements:

```sql
CREATE FUNCTION add_vat(price DECIMAL(10,2), rate DECIMAL(4,2)) RETURNS DECIMAL(10,2) DETERMINISTIC
LANGUAGE PYTHON AS $$
    return price * (1 + rate / 100)
$$;
SELECT add_vat(100, 20) AS gross;
```

```text
+--------+
| gross  |
+--------+
| 120.00 |
+--------+
```

- **The parts:** name, parameters, `RETURNS` (functions), the usual characteristics (`DETERMINISTIC`,
  `SQL SECURITY`, `COMMENT`, ...), `LANGUAGE JAVASCRIPT`, `PYTHON` or `JAVA`, optionally `USING` libraries, and the
  body between `$$` and `$$`.
- **Parameters** are variables of the body, of the language's types (each language page has the table: a
  `DECIMAL` is a `Decimal` in Python, a string in JavaScript, a `BigDecimal` in Java). The result is converted back
  to the declared SQL type.
- **`CREATE` checks the body:** a syntax error refuses the routine and names the line (Java is compiled; Python also
  warns about names that are never defined). See [Debugging routines](../debugging/index.md).
- **Routines are ordinary routines for the server:** `SHOW CREATE FUNCTION`, `information_schema.ROUTINES`
  (`EXTERNAL_LANGUAGE`), `DROP`, privileges, replication of `CREATE` and of the data they change.

```sql
SELECT ROUTINE_NAME, ROUTINE_TYPE, EXTERNAL_LANGUAGE FROM information_schema.ROUTINES
 WHERE ROUTINE_SCHEMA = 'app';
```

```text
+--------------+--------------+-------------------+
| ROUTINE_NAME | ROUTINE_TYPE | EXTERNAL_LANGUAGE |
+--------------+--------------+-------------------+
| add_vat      | FUNCTION     | PYTHON            |
+--------------+--------------+-------------------+
```

## Functions and procedures

| | Function | Procedure |
|---|---|---|
| Called | in expressions: `SELECT f(x)`, `WHERE`, generated values, triggers | `CALL p(...)` |
| Returns | one value | `OUT` and `INOUT` parameters; result sets to the client |
| SQL statements | no (the server refuses them in functions and triggers) | yes: queries, changes, transactions |

Each language gives the body an object `session` for SQL (see the language pages). In a function it exists, but
running a statement fails:

```sql
CREATE FUNCTION count_tables() RETURNS INT LANGUAGE JAVASCRIPT AS $$
  return session.runSql('SELECT COUNT(*) FROM information_schema.TABLES').fetchOne()[0]
$$;
SELECT count_tables();
```

```text
ERROR 6113 (HY000): JavaScript> SQL statements are not allowed in stored functions and triggers: use a stored procedure (count_tables, line 2)
```

The same code in a procedure works:

```sql
CREATE PROCEDURE count_tables_p(OUT n INT) READS SQL DATA LANGUAGE JAVASCRIPT AS $$
  n = session.runSql('SELECT COUNT(*) FROM information_schema.TABLES').fetchOne()[0]
$$;
CALL count_tables_p(@n);
SELECT @n > 0 AS has_tables;
```

```text
+------------+
| has_tables |
+------------+
|          1 |
+------------+
```

## Libraries

A library is code that routines share: functions, classes, constants. It is created once and named in the `USING`
clause of the routines that need it:

```sql
CREATE LIBRARY prices LANGUAGE JAVASCRIPT AS $$
  export function withVat(price, rate) { return Math.round(price * (100 + rate)) / 100 }
$$;
CREATE FUNCTION gross_js(price DOUBLE) RETURNS DOUBLE LANGUAGE JAVASCRIPT USING (prices)
AS $$ return prices.withVat(price, 20) $$;
SELECT gross_js(100) AS gross;
```

```text
+-------+
| gross |
+-------+
|   120 |
+-------+
```

Libraries can be written in JavaScript, Python or Java, and a routine can use a library of any of the three
languages, as well as the other languages' packages, numpy and Java classes: see
[Languages together](polyglot.md). The [Libraries](libraries.md) page has the rules.

Third-party packages (npm, PyPI, Maven) are installed by the DBA with `mle_package`, not by routines: see the
[tutorial](../tutorial/index.md#7-third-party-packages-the-dba).

## Where the code runs

Routines don't run inside mysqld. They run in **workers**: separate processes, sandboxed by the operating system,
that MLE starts and stops.

- **A worker per session and account.** A session gets a worker when it first calls a routine, for the account the
  routine runs as: its definer, or the caller with `SQL SECURITY INVOKER`. Routines of different accounts never share
  a worker, so one account's code can't see another's data.
- **SQL goes back to the server,** in the caller's session: the same transaction, the same locks, the same
  privileges as an SQL routine of the same account.
- **The sandbox:** a routine can't read or write files, open network connections, start programs or see other
  processes. It gets the memory the DBA allows (`mle.memory_max`); `KILL` stops it.
- **A worker that fails** (out of memory, a crash in a native library) ends with an error for that call; mysqld and
  other sessions go on.

## What lives how long

| What | Lives for |
|---|---|
| Variables of the body, and what it stores in global variables | one call |
| The last call's output and traceback (`mle_session_state('last_output')`, `'traceback'`) | until the next call |
| Output (`print`, `console.log`): `mle_session_state('stdout')`, `'stderr'` | the session |
| Loaded routines | the session |
| Libraries' module state, Python modules, Java static fields | the worker: it can be kept after the session and serve later sessions of the **same account** |

Every call starts with fresh global variables:

```sql
CREATE FUNCTION calls_so_far() RETURNS INT LANGUAGE JAVASCRIPT AS $$
  globalThis.count = (globalThis.count ?? 0) + 1
  return globalThis.count
$$;
SELECT calls_so_far() AS first, calls_so_far() AS second;
```

```text
+-------+--------+
| first | second |
+-------+--------+
|     1 |      1 |
+-------+--------+
```

A library's module is loaded once per worker, so a value it keeps survives calls:

```sql
CREATE LIBRARY counter LANGUAGE JAVASCRIPT AS $$
  let n = 0
  export function next() { return ++n }
$$;
CREATE FUNCTION next_number() RETURNS INT LANGUAGE JAVASCRIPT USING (counter) AS $$ return counter.next() $$;
SELECT next_number() AS a, next_number() AS b, next_number() AS c;
```

```text
+------+------+------+
| a    | b    | c    |
+------+------+------+
|    1 |    2 |    3 |
+------+------+------+
```

Use this for caches (a parsed model, a compiled regular expression), not for data: a worker can be replaced at any
time (a memory limit, `mle_session_reset()`, a restart), and the next one starts from zero. Data belongs in tables.

## Privileges

| To | Needs |
|---|---|
| create a routine or a library in a language | `CREATE ROUTINE` on the schema and `CREATE_JAVASCRIPT_ROUTINE`, `CREATE_PYTHON_ROUTINE` or `CREATE_JAVA_ROUTINE` |
| call a routine | `EXECUTE`, as for SQL routines |
| change what MLE may use (memory, languages, packages) | the DBA: system variables and `mle_package` |

The language privileges are separate because code in these languages can do much more than SQL can (compute,
parse, call libraries), within the sandbox.

## Two builds

The DBA installs one of two builds (see [Install](../install/index.md)):

- **The JVM build** runs all three languages in one worker, where each can call the others' code (see
  [Languages together](polyglot.md)).
- **The Python-only native build** runs only Python routines, with about half the memory per worker and workers
  that start about ten times faster.

The status variable `mle_languages_supported` lists the languages the server runs.
