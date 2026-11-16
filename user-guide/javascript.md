# JavaScript routines

A JavaScript routine's body is the body of a JavaScript function: statements, `return` for a function's result.
The JavaScript API is that of MySQL's JavaScript stored programs, so routines written for them run unchanged.
JavaScript needs the JVM build of MLE.

```sql
CREATE FUNCTION initials(full_name VARCHAR(100)) RETURNS VARCHAR(10) DETERMINISTIC
LANGUAGE JAVASCRIPT AS $$
  return full_name.split(/\s+/).filter(w => w).map(w => w[0].toUpperCase() + '.').join('')
$$;
SELECT initials('ada  king lovelace') AS initials;
```

```text
+----------+
| initials |
+----------+
| A.K.L.   |
+----------+
```

The language is modern JavaScript (ECMAScript 2024): classes, arrow functions, destructuring, `Map`/`Set`,
`BigInt`, regular expressions, `Intl` for locale-aware formatting, `JSON`. It is not Node.js: there is no `fs`,
`process` or `http` (routines can't reach files or the network anyway). npm packages work when the DBA has
installed them.

## The body

- **Strict mode:** an assignment to an undeclared variable is an error, not a new global.
- **Parameters are variables** with the parameters' names; the body can change them.
- **`return`** gives a function's result; a function that ends without `return` returns `NULL`.
- **Fresh globals per call:** what a call stores in `globalThis` is gone in the next call. Libraries keep state
  across calls (see [Concepts](concepts.md#what-lives-how-long)).
- **`await`:** a body that uses `await` runs as an `async` function; the call returns when its promise settles.

```sql
CREATE FUNCTION js_words(txt TEXT) RETURNS JSON DETERMINISTIC LANGUAGE JAVASCRIPT AS $$
  const counts = new Map()
  for (const w of txt.toLowerCase().match(/[a-z']+/g) ?? [])
    counts.set(w, (counts.get(w) ?? 0) + 1)
  const top = [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3)
  return { words: [...counts.values()].reduce((a, b) => a + b, 0), top: Object.fromEntries(top) }
$$;
SELECT js_words('the cat and the hat and the bat') AS stats;
```

```text
+-----------------------------------------------------+
| stats                                               |
+-----------------------------------------------------+
| {"top": {"and": 2, "bat": 1, "the": 3}, "words": 8} |
+-----------------------------------------------------+
```

## Parameters and results

Arguments arrive as these JavaScript values; a result is converted back to the declared type.

| SQL type | JavaScript value | Notes |
|---|---|---|
| `TINYINT` ... `BIGINT`, `YEAR` | number | integers beyond ±2<sup>53</sup> arrive as strings (see options below) |
| `BIT` | number, or BigInt beyond 2<sup>53</sup> | |
| `FLOAT`, `DOUBLE` | number | |
| `DECIMAL` | string | exact; `Number(x)` for arithmetic, or the `decimal_type` option |
| `CHAR`, `VARCHAR`, `TEXT`, `ENUM` | string | |
| `SET` | `Set` of strings | |
| `BINARY`, `VARBINARY`, `BLOB` | `Uint8Array` | |
| `JSON` | the parsed value: object, array, string, number, boolean | |
| `DATE`, `DATETIME`, `TIMESTAMP` | `Date` | in the session's time zone |
| `TIME` | string, `'hh:mm:ss[.ffffff]'` | |
| `VECTOR` | `Float32Array` | |
| `NULL` | `null` | `null` and `undefined` give `NULL` back |

Results: a number for an integer type is rounded (`Math.round`), a string for a number type is parsed, an object or
array for `JSON` is serialised, a `Date` serves for the date and time types. A value that doesn't fit the type is an
error (6000, `MLE-Type>`):

```sql
CREATE FUNCTION too_big() RETURNS TINYINT LANGUAGE JAVASCRIPT AS $$ return 300 $$;
SELECT too_big();
```

```text
ERROR 6000 (HY000): MLE-Type> Cannot convert value '300' to TINYINT from MLE in 'too_big()'
```

**Large integers and decimals: session options.** `mle_set_session_state` changes how integers and `DECIMAL`
values arrive in JavaScript, for the session's routines loaded afterwards:

| Option | Values |
|---|---|
| `integer_type` | `UNSAFE_STRING` (default: numbers, strings beyond ±2<sup>53</sup>), `UNSAFE_BIGINT` (numbers, BigInt beyond), `STRING` (always strings), `BIGINT` (always BigInt) |
| `decimal_type` | `STRING` (default), `NUMBER` |

```sql
CREATE FUNCTION js_type(v BIGINT) RETURNS VARCHAR(20) LANGUAGE JAVASCRIPT AS $$ return typeof v $$;
SELECT js_type(1) AS small, js_type(9007199254740993) AS large;
```

```text
+--------+--------+
| small  | large  |
+--------+--------+
| number | string |
+--------+--------+
```

```sql
SELECT mle_session_reset();
SELECT mle_set_session_state('{"integer_type": "BIGINT"}');
SELECT js_type(1) AS small, js_type(9007199254740993) AS large;
```

```text
+------------------------------------------+
| mle_session_reset()                      |
+------------------------------------------+
| The session state is successfully reset. |
+------------------------------------------+
+-----------------------------------------------------+
| mle_set_session_state('{"integer_type": "BIGINT"}') |
+-----------------------------------------------------+
|                                                   1 |
+-----------------------------------------------------+
+--------+--------+
| small  | large  |
+--------+--------+
| bigint | bigint |
+--------+--------+
```

The options apply to routines loaded after them (`mle_session_reset()` first if the session has already called
routines), and hold for all the session's JavaScript routines until the session ends or `mle_session_reset()`. A
routine that does arithmetic on an integer parameter (`n + 1`) then fails, since BigInt needs `n + 1n`. The SQL API
has the same options per session or statement, in camelCase (`integerType`, `decimalType`): see
[SQL from routines](sql.md). Back to the defaults:

```sql
SELECT mle_session_reset();
```

```text
+------------------------------------------+
| mle_session_reset()                      |
+------------------------------------------+
| The session state is successfully reset. |
+------------------------------------------+
```

## Procedures: OUT and INOUT parameters

`OUT` and `INOUT` parameters are variables the body assigns; `OUT` parameters start as `null`. The body may also
`return` early (its value is ignored).

```sql
CREATE PROCEDURE split_name(IN full_name VARCHAR(100), OUT first VARCHAR(50), OUT last VARCHAR(50),
                            INOUT calls INT)
LANGUAGE JAVASCRIPT AS $$
  calls = (calls ?? 0) + 1
  const parts = full_name.trim().split(/\s+/)
  if (parts.length < 2) { first = parts[0]; return }
  first = parts.slice(0, -1).join(' ')
  last = parts[parts.length - 1]
$$;
SET @calls = 0;
CALL split_name('Ada King Lovelace', @first, @last, @calls);
CALL split_name('Plato', @first2, @last2, @calls);
SELECT @first, @last, @first2, @last2, @calls;
```

```text
+----------+----------+---------+--------+--------+
| @first   | @last    | @first2 | @last2 | @calls |
+----------+----------+---------+--------+--------+
| Ada King | Lovelace | Plato   | NULL   |      2 |
+----------+----------+---------+--------+--------+
```

## Globals

| Name | What |
|---|---|
| `session` | SQL in the caller's session: `session.runSql(sql, params)`, `session.prepare(sql)`, transactions (procedures only). See [SQL from routines](sql.md). |
| `mysql` | `mysql.rand()`, `mysql.sleep(seconds)`, `mysql.uuid()`, `mysql.isUUID(s)`, `mysql.arg()`, and the constants `mysql.Type`, `mysql.IntegerType`, `mysql.DecimalType` |
| `SqlError` | an error with an SQL state and error number (below) |
| `console` | `console.log`, `console.info` and `console.debug` write to the session's stdout, `console.error` and `console.warn` to its stderr; `console.clear()` clears both |
| `importPython` | `importPython('numpy')`: a Python module (numpy, PyPI packages the DBA installed) |
| `Java` | `Java.type('java.util.UUID')`: Java classes, including jars the DBA installed (see [Java libraries](../java-libraries/index.md)) |
| `Polyglot` | `Polyglot.eval('python', code)`: code of another language |

## Errors

An exception that leaves the body fails the statement with error 6113, the exception's message and where it
happened:

```sql
CREATE FUNCTION parse_price(s VARCHAR(20)) RETURNS DECIMAL(10,2) LANGUAGE JAVASCRIPT AS $$
  const v = Number(s.replace(',', '.'))
  if (Number.isNaN(v)) throw new Error(`not a price: ${s}`)
  return v
$$;
SELECT parse_price('12,50') AS ok;
SELECT parse_price('twelve');
```

```text
+-------+
| ok    |
+-------+
| 12.50 |
+-------+
ERROR 6113 (HY000): JavaScript> Error: not a price: twelve (parse_price, line 3)
```

To raise an SQL error of your own, with its SQL state and error number (as SQL's `SIGNAL`), throw a `SqlError`:
`new SqlError(sqlState, message, errorNumber)`. In a procedure the client gets exactly that error, and an SQL
handler can catch it:

```sql
CREATE PROCEDURE check_stock(IN qty INT) LANGUAGE JAVASCRIPT AS $$
  if (qty < 0) throw new SqlError('45001', 'quantity must not be negative', 5001)
$$;
CALL check_stock(-1);
```

```text
ERROR 5001 (45001): quantity must not be negative
```

An SQL statement that fails inside a routine throws a `SqlError` with the statement's `code`, `sqlState` and
`message`; the routine can catch it, or let it fail the call. [Debugging routines](../debugging/index.md) shows
tracebacks and output.

## Libraries and modules

A JavaScript library is an ES module: what it `export`s, routines can use. A routine names its libraries in `USING`
and gets each as an object (the library's name, or `AS` an alias):

```sql
CREATE LIBRARY textkit LANGUAGE JAVASCRIPT AS $$
  export function slug(s) {
    return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
            .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  }
  export const VERSION = '1.0'
$$;
CREATE FUNCTION slug(s VARCHAR(200)) RETURNS VARCHAR(200) DETERMINISTIC LANGUAGE JAVASCRIPT
  USING (textkit AS tk)
AS $$ return tk.slug(s) + ' (v' + tk.VERSION + ')' $$;
SELECT slug('Crème Brûlée à la carte') AS slug;
```

```text
+--------------------------------+
| slug                           |
+--------------------------------+
| creme-brulee-a-la-carte (v1.0) |
+--------------------------------+
```

- A library can import another with `import { f } from '/schema/library'`.
- A routine can also `await import('/schema/library')`, for a library of its own schema; libraries of other schemas
  need `USING`.
- npm packages the DBA installed: `require('name')` (CommonJS) or `await import('name')` (ES modules).
- Python and Java libraries in `USING` work the same way, and numpy and Java classes are a call away: see
  [Languages together](polyglot.md).

## Not available

- Node.js and browser APIs: files, network, timers (`setTimeout` is not defined), `structuredClone`.
- State kept between calls in globals: use a library (a cache) or a table (data).
- Debugging with breakpoints: see [Debugging routines](../debugging/index.md) for what there is.
