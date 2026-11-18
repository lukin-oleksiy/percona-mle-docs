# SQL from routines

A procedure runs SQL through `session`: queries, changes, transactions, result sets for the client. The statements run
in the caller's session: the same transaction, locks, temporary tables and session variables, with the privileges of
the account the routine runs as (its definer, or the caller with `SQL SECURITY INVOKER`). Functions can't run SQL (the
server refuses statements in functions and triggers).

The API is the same in the three languages, in each language's style:

| | JavaScript | Python | Java |
|---|---|---|---|
| the session | `session` | `session` | `Mle.session()` |
| run a statement | `session.runSql(sql, params)` | `session.run_sql(sql, params)` | `Mle.session().runSql(sql, params...)` |
| prepare | `session.prepare(sql)` | `session.prepare(sql)` | `Mle.session().prepare(sql)` |
| bind, execute | `.bind(v1, v2).execute()` | `.bind(v1, v2).execute()` | `.bind(v1, v2).execute()` |
| one row, all rows | `fetchOne()`, `fetchAll()` | `fetch_one()`, `fetch_all()` | `fetchOne()`, `fetchAll()` |
| a column of a row | `row.name`, `row['name']`, `row[0]` | `row.name`, `row['name']`, `row[0]` | `row.getField("name")`, `row.get(0)` |
| transactions | `startTransaction()`, `commit()`, `rollback()` | `start_transaction()`, `commit()`, `rollback()` | the same as JavaScript |
| an SQL error | `SqlError` (`code`, `sqlState`, `message`) | `SqlError` (`code`, `sql_state`, `message`) | `MleSqlException` |

Values come and go in the language's types, as for parameters (see the language pages): `?` placeholders take the
language's values, and rows give them back.

## Queries

```sql
CREATE TABLE orders (id INT PRIMARY KEY AUTO_INCREMENT, customer VARCHAR(20), amount DECIMAL(8,2));
INSERT INTO orders (customer, amount) VALUES ('ann', 20.00), ('bob', 35.50), ('ann', 12.25), ('cid', 8.00);
CREATE PROCEDURE top_customer(OUT who VARCHAR(20), OUT total DECIMAL(10,2)) READS SQL DATA
LANGUAGE PYTHON AS $$
    res = session.run_sql('SELECT customer, SUM(amount) AS total FROM orders GROUP BY customer '
                          'ORDER BY total DESC LIMIT 1')
    row = res.fetch_one()
    who, total = row.customer, row.total
$$;
CALL top_customer(@who, @total);
SELECT @who, @total;
```

```text
+------+--------+
| @who | @total |
+------+--------+
| bob  |  35.50 |
+------+--------+
```

- **Parameters:** `?` in the statement, values in a list (`runSql(sql, [a, b])`), or `prepare(sql).bind(a, b)`. Never
  build SQL text from values.
- **Rows:** iterate the result (`for (const row of res)`, `for row in res`), or `fetchOne()` until it gives nothing,
  or `fetchAll()`.
- **Results:** `getAffectedItemsCount()`, `getAutoIncrementValue()`, `getWarnings()`, `getColumnNames()`;
  `nextResult()` for a statement with several results (a `CALL`).

## Changes and transactions

```sql
CREATE PROCEDURE add_order(IN who VARCHAR(20), IN amount DECIMAL(8,2), OUT new_id INT) MODIFIES SQL DATA
LANGUAGE JAVASCRIPT AS $$
  session.startTransaction()
  try {
    const r = session.runSql('INSERT INTO orders (customer, amount) VALUES (?, ?)', [who, amount])
    new_id = r.getAutoIncrementValue()
    const open = session.runSql('SELECT COUNT(*) AS n FROM orders WHERE customer = ?', [who]).fetchOne().n
    if (open > 3) throw new SqlError('45003', `too many orders for ${who}`, 5003)
    session.commit()
  } catch (e) {
    session.rollback()
    throw e
  }
$$;
CALL add_order('ann', 5.00, @id);
SELECT @id;
CALL add_order('ann', 6.00, @id);
SELECT COUNT(*) FROM orders WHERE customer = 'ann';
```

```text
+------+
| @id  |
+------+
|    5 |
+------+
ERROR 5003 (45003): too many orders for ann
+----------+
| COUNT(*) |
+----------+
|        3 |
+----------+
```

- The routine runs in the caller's transaction: without `startTransaction()`, its changes are part of whatever the
  caller does (and of `autocommit`).
- `setSavepoint()`, `rollbackTo(name)`, `releaseSavepoint(name)` work as in SQL.
- Statements and their changes are binary-logged and replicated as for an SQL procedure; the replica needs no MLE.

## Many rows at once: executeMany

`executeMany(rows)` executes a statement once per row of values, without a trip from the routine to mysqld and back
for every row: in Python `execute_many` (also `executemany`), on a prepared statement or on the session (which
prepares the statement and deallocates it afterwards).

```sql
CREATE TABLE scores (id INT PRIMARY KEY, score DOUBLE);
CREATE PROCEDURE save_scores(IN n INT) MODIFIES SQL DATA LANGUAGE PYTHON AS $$
    rows = [(i, i / n) for i in range(n)]
    session.start_transaction()
    try:
        session.execute_many('INSERT INTO scores (id, score) VALUES (?, ?)', rows)
        session.commit()
    except SqlError as e:
        session.rollback()
        raise SqlError('45000', f'row {e.row}: {e.message}', 5001)
$$;
CALL save_scores(1000);
SELECT COUNT(*), ROUND(SUM(score), 1) FROM scores;
CALL save_scores(3);
SELECT COUNT(*) FROM scores;
```

```text
+----------+----------------------+
| COUNT(*) | ROUND(SUM(score), 1) |
+----------+----------------------+
|     1000 |                499.5 |
+----------+----------------------+
ERROR 5001 (45000): row 0: Duplicate entry '0' for key 'scores.PRIMARY'
+----------+
| COUNT(*) |
+----------+
|     1000 |
+----------+
```

- Each row is a list (JavaScript array, Python list or tuple, Java `Object[]`) with one value per parameter marker,
  as for `bind()`. The result's `getAffectedItemsCount()` is the sum over the rows.
- **It stops at the first failing row**: the error's `row` (`getRow()` in Java) says which, counted from 0. The rows
  before it stay written: the transaction is yours. Run a batch in a transaction, as above, to keep it all or
  nothing; without one, `autocommit` commits every row on its own (slower too: a log flush per row).
- For statements that return no rows (INSERT, UPDATE, DELETE, REPLACE, ...): a statement that returns a result set
  is refused after its first row.
- It saves the trip from the routine to mysqld per row: for rows with a VECTOR(768), 1,000 rows took 47 ms
  instead of 52 ms (exp note 51).
- **Multi-row statements:** an `INSERT` or `REPLACE` of one row of values (each a `?`, `NULL`, `DEFAULT` or a
  literal; optionally `AS alias` and `ON DUPLICATE KEY UPDATE` without `?`) into an InnoDB table, with 256 rows
  or more, runs as statements of up to 1,000 rows, as Connector/J's `rewriteBatchedStatements` does: the server
  then starts one statement per chunk, not per row (1,000 small rows: 11 ms instead of 13 ms; exp note 52).
  Smaller batches don't win back the cost of preparing the longer statement in every call. Affected rows, triggers and row events stay the same, and so do the
  error rules above (a failing chunk is repeated row by row to find the row). What you can notice: `NOW()` and
  `CURRENT_TIMESTAMP` defaults have one value per statement, and `LAST_INSERT_ID()` afterwards is the first id of
  the last statement. MLE remembers that a table is InnoDB for the worker's session; if it was changed to
  another engine since and a statement fails, the error's `row` is the first row of that statement.
  `batchRewrite: false` (`prepare(sql, {batchRewrite: false})`, or
  `executeMany(sql, rows, {batchRewrite: false})`) runs one statement per row. Other statements always run one
  statement per row.
- `executeMany` is MLE's addition: Oracle's MySQL JavaScript API has no batch method.

## Result sets for the client

A procedure can send a result set to the client, as an SQL procedure's `SELECT` does: the option
`passResultToClient` (Python: `pass_result_to_client`).

```sql
CREATE PROCEDURE orders_of(IN who VARCHAR(20)) READS SQL DATA LANGUAGE PYTHON AS $$
    session.prepare('SELECT id, amount FROM orders WHERE customer = ? ORDER BY id',
                    {'pass_result_to_client': True}).bind(who).execute()
$$;
CALL orders_of('bob');
```

```text
+----+--------+
| id | amount |
+----+--------+
|  2 |  35.50 |
+----+--------+
```

## Many rows into numpy: fetch_columns

In Python, `fetch_columns()` returns the remaining rows column by column, ready for numpy: a dict by column name,
numbers as numpy arrays (`int64`, `float64`), `VECTOR` columns as one 2-D `float32` matrix, other columns (and
columns with `NULL`s) as lists. It is much faster than reading rows one by one for large results.

```sql
CREATE PROCEDURE amount_stats(OUT s JSON) READS SQL DATA LANGUAGE PYTHON AS $$
    import numpy as np
    cols = session.run_sql('SELECT customer, amount + 0e0 AS amount FROM orders').fetch_columns()
    a = cols['amount']
    s = {'n': len(a), 'mean': round(float(np.mean(a)), 2), 'type': type(a).__name__}
$$;
CALL amount_stats(@s);
SELECT @s;
```

```text
+--------------------------------------------+
| @s                                         |
+--------------------------------------------+
| {"n": 5, "mean": 16.15, "type": "ndarray"} |
+--------------------------------------------+
```

JavaScript has `fetchColumns()` too (an object of arrays by column name).

## Errors

A failing statement throws the language's SQL error with the server's error number, SQL state and message. Catch it
to handle it; uncaught, the client gets the statement's error unchanged:

```sql
CREATE PROCEDURE safe_insert(IN i INT, OUT r TEXT) MODIFIES SQL DATA LANGUAGE PYTHON AS $$
    try:
        session.run_sql('INSERT INTO orders (id, customer, amount) VALUES (?, ?, ?)', [i, 'dup', 1])
        r = 'inserted'
    except SqlError as e:
        r = 'error %d (%s): %s' % (e.code, e.sql_state, e.message)
$$;
CALL safe_insert(1, @r);
SELECT @r;
```

```text
+------------------------------------------------------------------+
| @r                                                               |
+------------------------------------------------------------------+
| error 1062 (23000): Duplicate entry '1' for key 'orders.PRIMARY' |
+------------------------------------------------------------------+
```

## Options

| Option | Values | Where |
|---|---|---|
| `passResultToClient` | `true`, `false` | a statement |
| `integerType` | `UNSAFE_STRING` (default), `UNSAFE_BIGINT`, `STRING`, `BIGINT` | JavaScript: how integers beyond 2<sup>53</sup> arrive |
| `decimalType` | `STRING` (default), `NUMBER` | JavaScript: how `DECIMAL` arrives |
| `charsetName` | `utf8mb4` | a statement |
| `batchRewrite` | `true` (default), `false` | `executeMany`: may run the rows as multi-row statements |

Options go with a statement (`runSql(sql, params, options)`, `prepare(sql, options)`) or for the session
(`session.setOptions({...})`). In Python their names can be snake_case.

## Limits

| Setting | Default | What |
|---|---|---|
| `mle.sql_result_max` | 64 MB | the largest result of a statement (the server holds it while the routine reads it) |
| `mle.sql_statements_max` | 1024 | statements a call keeps open |

Rows reach the routine in chunks as it fetches them, so the routine holds only what it keeps. The server holds one
result at a time: rows left unread when the routine runs its next statement are read into the routine's worker
first (close a result you do not need). For results larger
than `mle.sql_result_max`, read them in parts (ranges of a key) or raise the setting.
