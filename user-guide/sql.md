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

Options go with a statement (`runSql(sql, params, options)`, `prepare(sql, options)`) or for the session
(`session.setOptions({...})`). In Python their names can be snake_case.

## Limits

| Setting | Default | What |
|---|---|---|
| `mle.sql_result_max` | 64 MB | the largest result of a statement without parameters (buffered whole) |
| `mle.sql_row_max` | 16 MB | the largest row of a prepared statement (read with a cursor) |
| `mle.sql_statements_max` | 1024 | statements a call keeps open |

For large results, prepare the statement (rows are then read as you fetch them) or use `fetch_columns`.
