# Python routines

A Python routine's body is the body of a Python function: statements, `return` for a function's result. Python's
standard library and numpy are there; PyPI packages work when the DBA has installed them. Python runs in both
builds of MLE (in the Python-only native build, it is the only language).

```sql
CREATE FUNCTION title_case(s VARCHAR(200)) RETURNS VARCHAR(200) DETERMINISTIC LANGUAGE PYTHON AS $$
    small = {'a', 'an', 'and', 'of', 'the', 'in', 'on'}
    words = s.lower().split()
    return ' '.join(w if i and w in small else w.capitalize() for i, w in enumerate(words))
$$;
SELECT title_case('the lord of the rings') AS title;
```

```text
+-----------------------+
| title                 |
+-----------------------+
| The Lord of the Rings |
+-----------------------+
```

The language is Python 3.12 (GraalPy).

## The body

- **Indentation:** the body is indented like a function body, or not at all; MLE removes the common indentation.
  The first statement may also start right after `$$`, with the rest indented below it.
- **Parameters are local variables** with the parameters' names.
- **`return`** gives a function's result; a body without `return` returns `NULL`.
- **The rules of a function body apply:** `from module import *` is not allowed (`import module`, or name what
  you import); `global` refers to the routine's globals.
- **Fresh globals per call:** what a call stores in its globals is gone in the next call. Modules (`import`, the
  `USING` libraries) are loaded once per worker and keep their state (see
  [Concepts](concepts.md#what-lives-how-long)).
- **`CREATE` checks names:** a name the body reads but never defines is a warning (see
  [Debugging routines](../debugging/index.md#mistakes-found-by-create)).

```sql
CREATE FUNCTION word_freq(txt TEXT, n INT) RETURNS JSON DETERMINISTIC LANGUAGE PYTHON AS $$
    import collections, re
    words = re.findall(r"[a-z']+", txt.lower())
    return dict(collections.Counter(words).most_common(n))
$$;
SELECT word_freq('the cat and the hat and the bat', 2) AS freq;
```

```text
+----------------------+
| freq                 |
+----------------------+
| {"and": 2, "the": 3} |
+----------------------+
```

## Parameters and results

| SQL type | Python value | Notes |
|---|---|---|
| `TINYINT` ... `BIGINT`, `YEAR`, `BIT` | `int` | exact, any size |
| `FLOAT`, `DOUBLE` | `float` | |
| `DECIMAL` | `decimal.Decimal` | exact |
| `CHAR`, `VARCHAR`, `TEXT`, `ENUM`, `SET` | `str` | a `SET` as `'a,b'` |
| `BINARY`, `VARBINARY`, `BLOB` | `bytes` | |
| `JSON` | `dict`, `list`, `str`, `int`, `float`, `bool` | |
| `DATE` | `datetime.date` | |
| `DATETIME`, `TIMESTAMP` | `datetime.datetime` | |
| `TIME` | `datetime.timedelta` | |
| `VECTOR` | numpy `float32` array | read-only, valid during the call: `v.copy()` to keep or change it (see [Vectors](vectors.md)) |
| `NULL` | `None` | |

A result is converted back to the declared type: the same types, plus `bool`, numpy scalars (`numpy.float64`) and
arrays (for `VECTOR`), a `set` for `SET`. A `dict` or `list` for `JSON` is serialised. A value that doesn't fit is
an error:

```sql
CREATE FUNCTION py_stats(xs JSON) RETURNS JSON DETERMINISTIC LANGUAGE PYTHON AS $$
    import statistics
    values = [float(x) for x in xs]
    return {'n': len(values), 'mean': statistics.fmean(values), 'stdev': round(statistics.stdev(values), 4)}
$$;
SELECT py_stats('[2, 4, 4, 4, 5, 5, 7, 9]') AS stats;
CREATE FUNCTION py_bad_result() RETURNS INT LANGUAGE PYTHON AS $$
    return 'many'
$$;
SELECT py_bad_result();
```

```text
+----------------------------------------+
| stats                                  |
+----------------------------------------+
| {"n": 8, "mean": 5.0, "stdev": 2.1381} |
+----------------------------------------+
ERROR 6000 (HY000): MLE-Type> cannot convert 'many' to INT in 'py_bad_result()'
```

## Procedures: OUT and INOUT parameters

`OUT` and `INOUT` parameters are variables the body assigns (as in JavaScript); `OUT` parameters start as `None`.

```sql
CREATE PROCEDURE min_max(IN xs JSON, OUT lo DOUBLE, OUT hi DOUBLE) LANGUAGE PYTHON AS $$
    if not xs:
        return
    lo, hi = min(xs), max(xs)
$$;
CALL min_max('[3, 1, 4, 1, 5]', @lo, @hi);
SELECT @lo, @hi;
```

```text
+------+------+
| @lo  | @hi  |
+------+------+
|    1 |    5 |
+------+------+
```

## Globals

| Name | What |
|---|---|
| `session` | SQL in the caller's session, in snake_case: `session.run_sql(sql, params)`, `session.prepare(sql)`, `fetch_one()`, `fetch_all()`, `fetch_columns()` (procedures only). See [SQL from routines](sql.md). |
| `mysql` | `mysql.rand()`, `mysql.sleep(seconds)`, `mysql.uuid()`, `mysql.is_uuid(s)` |
| `SqlError` | an exception with an SQL state and error number (below) |
| `print`, `sys.stdout`, `sys.stderr` | the session's console (see [Debugging routines](../debugging/index.md#seeing-what-a-routine-printed)) |
| `import_library('schema.name')`, `import_js(name)` | a database library, or an npm package the DBA installed, at run time (`USING` is the usual way for libraries) |
| `java` (after `import java`) | `java.type('java.util.UUID')`: Java classes, including the DBA's jars (JVM build only) |

## Errors

An exception that leaves the body fails the statement with error 6113, the exception and where it happened:

```sql
CREATE FUNCTION parse_ratio(s VARCHAR(20)) RETURNS DOUBLE LANGUAGE PYTHON AS $$
    num, den = s.split('/')
    return int(num) / int(den)
$$;
SELECT parse_ratio('3/4') AS ok;
SELECT parse_ratio('3/0');
```

```text
+------+
| ok   |
+------+
| 0.75 |
+------+
ERROR 6113 (HY000): Python> ZeroDivisionError: division by zero (parse_ratio, line 3)
```

`raise SqlError(sql_state, message, error_number)` raises an SQL error of your own, as SQL's `SIGNAL` does:

```sql
CREATE PROCEDURE check_qty(IN qty INT) LANGUAGE PYTHON AS $$
    if qty < 0:
        raise SqlError('45001', 'quantity must not be negative', 5001)
$$;
CALL check_qty(-1);
```

```text
ERROR 5001 (45001): quantity must not be negative
```

An SQL statement that fails inside a routine raises a `SqlError`; catch it with `except SqlError as e` (`e.code`,
`e.sql_state`, `e.message`), or let it fail the call.

## Libraries and modules

A Python library is a module: the routine names it in `USING` and gets it as a module object.

```sql
CREATE LIBRARY geo LANGUAGE PYTHON AS $$
import math

EARTH_KM = 6371.0

def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))
$$;
CREATE FUNCTION km(lat1 DOUBLE, lon1 DOUBLE, lat2 DOUBLE, lon2 DOUBLE) RETURNS DOUBLE DETERMINISTIC
LANGUAGE PYTHON USING (geo) AS $$
    return round(geo.distance_km(lat1, lon1, lat2, lon2), 1)
$$;
SELECT km(50.45, 30.52, 52.52, 13.40) AS kyiv_berlin_km;
```

```text
+----------------+
| kyiv_berlin_km |
+----------------+
|         1204.6 |
+----------------+
```

- A library's code is written at module level (no common indentation is removed).
- JavaScript and Java libraries in `USING` work too (JVM build), as do npm packages and Java classes: see
  [Languages together](polyglot.md).
- PyPI packages the DBA installed: a plain `import`.

## Threads and limits

- **No threads:** `threading` and `concurrent.futures` raise `RuntimeError: threads are not available in MLE
  routines`. numpy's own native code may still use several cores.
- **Memory:** numpy arrays count against the worker's memory limit (`mle.memory_max`). A routine that builds large
  arrays in a loop should reuse them.
- **Start:** a worker loads Python on its first Python routine (seconds on the JVM build, a fraction of a second on
  the native one); the DBA can have pool workers load it in advance (`mle.pool_prewarm`).
