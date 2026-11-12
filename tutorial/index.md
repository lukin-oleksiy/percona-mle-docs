# Tutorial: stored programs in JavaScript, Python and Java

In about an hour you write stored functions and procedures in three languages on a Percona Server with MLE
(the multi-language engine, `component_mle`), run SQL from them, work with vectors in Python and numpy, share code
between languages, install a third-party package as the DBA, and see what happens when a routine misbehaves.

Everything runs in Docker; nothing is installed on your machine. Every command and every output on this page is
checked by running the page (`check_tutorial.py`), so what you see is what you should get.

**You need:** Docker with Compose on Linux (tested on Linux 7.0; MLE needs Landlock, kernel 6.12 or newer); about
8 GB of memory for the container; network access for step 7.

## 1. Start the server

Get the images (`percona-mle:dev` and, for step 7, `percona-mle-tools:dev`): load the files you were given, or
build them from the sources (`docker/README.md` in the percona-mle repository):

<!-- check: skip -->
```sh
docker load -i percona-mle.tar
docker load -i percona-mle-tools.tar
```

In the directory with `compose.yaml` (`docker/` in the percona-mle repository), start the server and wait until
it is ready (the first start initialises the data directory, about 20 seconds):

```sh
docker compose up -d --quiet-pull 2>/dev/null
for i in $(seq 60); do docker compose exec mysql mysqladmin -uroot -pmle ping 2>/dev/null && break; sleep 2; done
```

```text
mysqld is alive
```

The first start installed `component_mle` and `component_lang_router` (the router sends each routine to the
component of its language) and created an application account `app` (password `app`) with its own database `app`
and the privileges to create routines in the three languages. Connect as `app`:

<!-- check: skip -->
```sh
docker compose exec mysql mysql -uapp -papp app
```

All SQL below runs in this client. (The outputs on this page leave out the client's "N rows in set" lines.)

```sql
SELECT VARIABLE_NAME, VARIABLE_VALUE FROM performance_schema.global_status
 WHERE VARIABLE_NAME IN ('mle_status', 'mle_languages_supported');
```

```text
+-------------------------+------------------------+
| VARIABLE_NAME           | VARIABLE_VALUE         |
+-------------------------+------------------------+
| mle_languages_supported | JavaScript,Python,Java |
| mle_status              | Active                 |
+-------------------------+------------------------+
```

## 2. A first function in JavaScript

A routine in another language is an ordinary `CREATE FUNCTION` or `CREATE PROCEDURE` with `LANGUAGE` and a body
between `$$` quotes. Arguments arrive as variables of the same names; `return` gives the result:

```sql
CREATE FUNCTION greet(name VARCHAR(40)) RETURNS VARCHAR(80) DETERMINISTIC
LANGUAGE JAVASCRIPT AS $$
  return `Hello, ${name}! You are ${name.length} characters long.`
$$;
SELECT greet('Percona');
```

```text
+--------------------------------------------+
| greet('Percona')                           |
+--------------------------------------------+
| Hello, Percona! You are 7 characters long. |
+--------------------------------------------+
```

`LANGUAGE JAVASCRIPT` routines are written as for Oracle's MySQL MLE (MySQL Enterprise): the same types, the same
SQL API, the same session functions. JavaScript has its full standard library — regular expressions, `Intl`, JSON:

```sql
CREATE FUNCTION slugify(title VARCHAR(200)) RETURNS VARCHAR(200) DETERMINISTIC
LANGUAGE JAVASCRIPT AS $$
  return title.normalize('NFKD').replace(/\p{M}/gu, '')
              .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
$$;
CREATE FUNCTION price_label(amount DECIMAL(10,2), currency CHAR(3)) RETURNS VARCHAR(40) DETERMINISTIC
LANGUAGE JAVASCRIPT AS $$
  return new Intl.NumberFormat('de-DE', { style: 'currency', currency }).format(amount)
$$;
SELECT slugify('Crème Brûlée & Café au Lait!') AS slug, price_label(1234.5, 'EUR') AS label;
```

```text
+---------------------------+---------------+
| slug                      | label         |
+---------------------------+---------------+
| creme-brulee-cafe-au-lait | 1.234,50 €    |
+---------------------------+---------------+
```

A function is called like any other: in `SELECT` lists, `WHERE` clauses, generated values, triggers.

When code throws, the error comes back to SQL; the JavaScript stack trace is kept for the session:

```sql
CREATE FUNCTION first_word(s TEXT) RETURNS TEXT DETERMINISTIC
LANGUAGE JAVASCRIPT AS $$
  return s.split(' ')[0].toUpperCase()
$$;
SELECT first_word(NULL);
SELECT TRIM(TRAILING '\n' FROM mle_session_state('stack_trace')) AS stack_trace;
```

```text
ERROR 6113 (HY000): JavaScript> TypeError: Cannot read property 'split' of null
+-------------------------+
| stack_trace             |
+-------------------------+
| <js> first_word:2:10-21 |
+-------------------------+
```

## 3. The same in Python and Java

**Python** bodies are indented like the body of a Python function. Python's standard library is there:

```sql
CREATE FUNCTION word_stats(txt TEXT) RETURNS JSON DETERMINISTIC
LANGUAGE PYTHON AS $$
    import collections, re
    words = re.findall(r"[a-z']+", txt.lower())
    common = collections.Counter(words).most_common(2)
    return {'words': len(words), 'most_common': [w for w, _ in common]}
$$;
SELECT word_stats('the quick brown fox jumps over the lazy dog the end') AS stats;
```

```text
+------------------------------------------------+
| stats                                          |
+------------------------------------------------+
| {"words": 11, "most_common": ["the", "quick"]} |
+------------------------------------------------+
```

A Python `dict` or `list` returned for a `JSON` result becomes JSON; `None` is `NULL`. `DECIMAL` arguments arrive
as Python `Decimal`, dates as `datetime.date`, and so on (the user guide has the type tables).

**Java** bodies are the body of a static method: statements, ending with `return`:

```sql
CREATE FUNCTION compound(principal DECIMAL(12,2), rate DECIMAL(6,4), years INT)
RETURNS DECIMAL(14,2) DETERMINISTIC
LANGUAGE JAVA AS $$
  BigDecimal f = BigDecimal.ONE.add(rate).pow(years);
  return principal.multiply(f).setScale(2, RoundingMode.HALF_EVEN);
$$;
SELECT compound(1000.00, 0.0350, 10) AS after_10_years;
```

```text
+----------------+
| after_10_years |
+----------------+
|        1410.60 |
+----------------+
```

The Java body is compiled when the routine is created, so mistakes are reported by `CREATE`:

```sql
CREATE FUNCTION broken(a INT) RETURNS INT DETERMINISTIC
LANGUAGE JAVA AS $$ return a +; $$;
```

```text
ERROR 6113 (HY000): Java> broken:1:12: illegal start of expression
```

All three languages can be mixed freely in one statement:

```sql
SELECT greet('MLE') AS javascript, JSON_EXTRACT(word_stats('a b a'), '$.words') AS python,
       compound(100, 0.1, 2) AS java;
```

```text
+----------------------------------------+--------+--------+
| javascript                             | python | java   |
+----------------------------------------+--------+--------+
| Hello, MLE! You are 3 characters long. | 3      | 121.00 |
+----------------------------------------+--------+--------+
```

## 4. SQL inside routines

Routines run SQL through the session object (Oracle's API: `session.runSql`, `session.prepare`, transactions; in
Python the same names in snake_case: `session.run_sql`). Create a small shop:

```sql
CREATE TABLE products (id INT PRIMARY KEY, name VARCHAR(40), price DECIMAL(8,2), stock INT);
INSERT INTO products VALUES (1, 'apple', 0.50, 100), (2, 'banana', 0.25, 150),
  (3, 'cherry', 4.80, 20), (4, 'date', 3.10, 0);
CREATE TABLE orders (id INT AUTO_INCREMENT PRIMARY KEY, product_id INT, qty INT, amount DECIMAL(10,2));
```

A JavaScript procedure that books an order in a transaction, checking the stock first. `OUT` parameters are
variables the routine assigns:

```sql
CREATE PROCEDURE place_order(IN product INT, IN qty INT, OUT amount DECIMAL(10,2))
LANGUAGE JAVASCRIPT AS $$
  session.startTransaction()
  const row = session.runSql('SELECT price, stock FROM products WHERE id = ? FOR UPDATE', [product])
                     .fetchOne()
  if (row === undefined || row.stock < qty) {
    session.rollback()
    throw new Error(`cannot order ${qty} of product ${product}`)
  }
  amount = row.price * qty
  session.runSql('UPDATE products SET stock = stock - ? WHERE id = ?', [qty, product])
  session.runSql('INSERT INTO orders (product_id, qty, amount) VALUES (?, ?, ?)', [product, qty, amount])
  session.commit()
$$;
CALL place_order(1, 10, @amount);
SELECT @amount;
CALL place_order(4, 1, @amount);
SELECT id, name, stock FROM products WHERE id IN (1, 4);
```

```text
+---------+
| @amount |
+---------+
|    5.00 |
+---------+
ERROR 6113 (HY000): JavaScript> Error: cannot order 1 of product 4
+----+-------+-------+
| id | name  | stock |
+----+-------+-------+
|  1 | apple |    90 |
|  4 | date  |     0 |
+----+-------+-------+
```

A Python procedure that reports per product, printing to the session's console (`print`; `console.log` in
JavaScript), which `mle_session_state('stdout')` returns:

```sql
CREATE PROCEDURE sales_report() READS SQL DATA
LANGUAGE PYTHON AS $$
    res = session.run_sql('''SELECT p.name, SUM(o.qty) AS qty, SUM(o.amount) AS revenue
                             FROM orders o JOIN products p ON p.id = o.product_id
                             GROUP BY p.name ORDER BY revenue DESC''')
    for row in res:
        print(f'{row.name:<8} {row.qty:>4} {row.revenue:>8}')
$$;
CALL place_order(3, 2, @amount);
CALL sales_report();
SELECT mle_session_state('stdout') AS console\G
```

```text
*************************** 1. row ***************************
console: cherry      2     9.60
apple      10     5.00
```

A procedure can also send a result set to the client, as an SQL procedure's `SELECT` does. With parameters,
prepare the statement and bind its values:

```sql
CREATE PROCEDURE low_stock(IN below INT) READS SQL DATA
LANGUAGE JAVASCRIPT AS $$
  session.prepare('SELECT name, stock FROM products WHERE stock < ? ORDER BY stock',
                  { passResultToClient: true }).bind(below).execute()
$$;
CALL low_stock(50);
```

```text
+--------+-------+
| name   | stock |
+--------+-------+
| date   |     0 |
| cherry |    18 |
+--------+-------+
```

## 5. Vectors with Python and numpy

Percona Server's `VECTOR` type and `DISTANCE` function find the nearest vectors; an HNSW index makes that fast. In
Python routines a `VECTOR` is a numpy array, so whatever comes after "find the nearest" — re-ranking, scores,
statistics — can run next to the data. A small catalogue with 4-dimensional embeddings (real ones have hundreds of
dimensions; an HNSW index needs a `BIGINT UNSIGNED` primary key):

```sql
CREATE TABLE items (id BIGINT UNSIGNED PRIMARY KEY, name VARCHAR(40), price DECIMAL(8,2), embedding VECTOR(4) NOT NULL,
  VECTOR KEY vk (embedding) TYPE hnsw (M = 16));
INSERT INTO items VALUES
  (1, 'red apple',   0.50, STRING_TO_VECTOR('[0.9, 0.1, 0.0, 0.2]')),
  (2, 'green apple', 0.45, STRING_TO_VECTOR('[0.8, 0.3, 0.0, 0.1]')),
  (3, 'banana',      0.25, STRING_TO_VECTOR('[0.1, 0.9, 0.1, 0.0]')),
  (4, 'apple juice', 2.10, STRING_TO_VECTOR('[0.7, 0.0, 0.6, 0.3]')),
  (5, 'orange',      0.60, STRING_TO_VECTOR('[0.3, 0.6, 0.2, 0.1]')),
  (6, 'apple pie',   6.90, STRING_TO_VECTOR('[0.8, 0.1, 0.3, 0.9]'));
```

SQL finds the nearest items to a query vector (cosine distance):

```sql
SET @q = STRING_TO_VECTOR('[1.0, 0.1, 0.0, 0.1]');
SELECT id, name, ROUND(DISTANCE(embedding, @q, 'COSINE'), 4) AS distance
  FROM items ORDER BY DISTANCE(embedding, @q, 'COSINE') LIMIT 4;
```

```text
+----+-------------+----------+
| id | name        | distance |
+----+-------------+----------+
|  1 | red apple   |    0.007 |
|  2 | green apple |   0.0331 |
|  4 | apple juice |   0.2545 |
|  6 | apple pie   |   0.2842 |
+----+-------------+----------+
```

A Python function receives `VECTOR` arguments as numpy arrays:

```sql
CREATE FUNCTION cosine(a VECTOR(4), b VECTOR(4)) RETURNS DOUBLE DETERMINISTIC
LANGUAGE PYTHON AS $$
    import numpy as np
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
$$;
SELECT name, ROUND(cosine(embedding, STRING_TO_VECTOR('[1.0, 0.1, 0.0, 0.1]')), 4) AS similarity
  FROM items WHERE id <= 3;
```

```text
+-------------+------------+
| name        | similarity |
+-------------+------------+
| red apple   |      0.993 |
| green apple |     0.9669 |
| banana      |     0.2065 |
+-------------+------------+
```

Re-ranking: take the nearest candidates from SQL, then score them by similarity *and* business data (here: cheaper
is better) in one numpy expression. `fetch_columns()` returns each column of a result as a numpy array — a
`VECTOR` column as one matrix:

```sql
CREATE PROCEDURE recommend(IN q VECTOR(4), IN k INT) READS SQL DATA
LANGUAGE PYTHON AS $$
    import numpy as np
    c = session.run_sql('''SELECT id, name, price, embedding FROM items
                           ORDER BY DISTANCE(embedding, ?, 'COSINE') LIMIT 5''', [q]).fetch_columns()
    m = c['embedding']
    similarity = m @ q / (np.linalg.norm(m, axis=1) * np.linalg.norm(q))
    price = np.array([float(p) for p in c['price']])
    score = 0.8 * similarity + 0.2 * (1 - price / price.max())
    for i in np.argsort(-score)[:k]:
        print(f"{c['name'][i]:<12} similarity {similarity[i]:.3f}  price {c['price'][i]}  score {score[i]:.3f}")
$$;
DO mle_session_reset('stdout');
CALL recommend(STRING_TO_VECTOR('[1.0, 0.1, 0.0, 0.1]'), 3);
SELECT mle_session_state('stdout') AS recommendations\G
```

```text
*************************** 1. row ***************************
recommendations: red apple    similarity 0.993  price 0.50  score 0.980
green apple  similarity 0.967  price 0.45  score 0.960
apple juice  similarity 0.746  price 2.10  score 0.736
```

(`mle_session_state('stdout')` collects the console of the whole session; `mle_session_reset('stdout')` clears
it, so that only the recommendations are shown. `\G` shows the multi-line text without a table frame.)

## 6. Libraries shared across languages

`CREATE LIBRARY` (Oracle's statement, here for all three languages) stores code that routines import with
`USING`, in any language. A JavaScript library used from Python:

```sql
CREATE LIBRARY textkit LANGUAGE JAVASCRIPT AS $$
  export function initials(name) { return name.split(/\s+/).map(w => w[0].toUpperCase() + '.').join(' ') }
$$;
CREATE FUNCTION py_initials(name VARCHAR(100)) RETURNS VARCHAR(20) DETERMINISTIC
LANGUAGE PYTHON USING (textkit) AS $$
    return textkit.initials(name)
$$;
SELECT py_initials('ada lovelace byron') AS initials;
```

```text
+----------+
| initials |
+----------+
| A. L. B. |
+----------+
```

A Python library used from JavaScript, and a Java library used from both:

```sql
CREATE LIBRARY stats LANGUAGE PYTHON AS $$
import statistics

def summary(xs):
    xs = [float(x) for x in xs]
    return {'mean': round(statistics.fmean(xs), 3), 'median': statistics.median(xs)}
$$;
CREATE LIBRARY money LANGUAGE JAVA AS $$
package money;
import java.math.BigDecimal;
import java.math.RoundingMode;
public final class Vat {
  public static String gross(String net, String rate) {
    return new BigDecimal(net).multiply(BigDecimal.ONE.add(new BigDecimal(rate)))
        .setScale(2, RoundingMode.HALF_EVEN).toPlainString();
  }
}
$$;
CREATE PROCEDURE price_summary(OUT r JSON) READS SQL DATA
LANGUAGE JAVASCRIPT USING (stats) AS $$
  const prices = session.runSql('SELECT price FROM products').fetchAll().map(row => row.price)
  const s = stats.summary(prices)
  r = { mean: s.get('mean'), median: s.get('median') }
$$;
CREATE FUNCTION js_gross(net DECIMAL(10,2)) RETURNS VARCHAR(20) DETERMINISTIC
LANGUAGE JAVASCRIPT USING (money) AS $$ return money.Vat.gross(String(net), '0.21') $$;
CREATE FUNCTION py_gross(net DECIMAL(10,2)) RETURNS VARCHAR(20) DETERMINISTIC
LANGUAGE PYTHON USING (money) AS $$
    return money.Vat.gross(str(net), '0.21')
$$;
CALL price_summary(@r);
SELECT @r AS javascript_uses_python, js_gross(100) AS javascript_uses_java, py_gross(19.99) AS python_uses_java;
```

```text
+--------------------------------+----------------------+------------------+
| javascript_uses_python         | javascript_uses_java | python_uses_java |
+--------------------------------+----------------------+------------------+
| {"mean": 2.163, "median": 1.8} | 121.00               | 24.19            |
+--------------------------------+----------------------+------------------+
```

Creating a library needs the privilege of its language, like a routine; using one needs `EXECUTE`.

## 7. Third-party packages (the DBA)

Routines cannot download or install anything. Packages from npm, PyPI and Maven are installed by the DBA, into a
volume the server reads (`mle.package_dir`), with the tool `mle_package` from the tools image. In a second terminal,
in the same directory:

<!-- output: varies -->
```sh
docker compose run --rm tools install python networkx 2>&1 | tail -1
```

```text
Done. In mysqld: SELECT mle_packages_refresh();
```

Workers load packages when they start; a worker that is running keeps what it has. Release your session's
worker (in your `app` client):

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

Then, as root (a third terminal: `docker compose exec mysql mysql -uroot -pmle`), tell MLE that new packages are
there; it refuses while sessions still hold workers:

<!-- as: root -->
```sql
SELECT mle_packages_refresh();
```

```text
+--------------------------------------------------+
| mle_packages_refresh()                           |
+--------------------------------------------------+
| New MLE workers will see the installed packages. |
+--------------------------------------------------+
```

Back in your `app` client, the next call starts a worker that sees the package. (A `JSON` argument arrives in
Python already parsed: here a list of lists.)

```sql
CREATE FUNCTION shortest_route(edges JSON, src VARCHAR(10), dst VARCHAR(10)) RETURNS JSON DETERMINISTIC
LANGUAGE PYTHON AS $$
    import networkx as nx
    g = nx.Graph()
    for a, b, km in edges:
        g.add_edge(a, b, weight=km)
    return nx.shortest_path(g, src, dst, weight='weight')
$$;
SELECT shortest_route('[["A","B",5],["B","C",4],["A","C",12],["C","D",3]]', 'A', 'D') AS route;
SELECT JSON_EXTRACT(mle_packages(), '$.packages') AS packages;
```

```text
+----------------------+
| route                |
+----------------------+
| ["A", "B", "C", "D"] |
+----------------------+
+----------------------------------------------------------------+
| packages                                                       |
+----------------------------------------------------------------+
| [{"name": "networkx", "version": "3.7", "language": "python"}] |
+----------------------------------------------------------------+
```

## 8. Safety: what a misbehaving routine can do

Routines run in separate, locked-down worker processes, one per session, never inside the server. A routine cannot
open files, start processes or reach the network:

```sql
CREATE FUNCTION try_escape() RETURNS TEXT DETERMINISTIC
LANGUAGE PYTHON AS $$
    try:
        return open('/etc/passwd').read()[:20]
    except OSError as e:
        return f'{type(e).__name__}: {e.strerror}'
$$;
SELECT try_escape();
```

```text
+------------------------------------+
| try_escape()                       |
+------------------------------------+
| PermissionError: Permission denied |
+------------------------------------+
```

An endless loop is stopped by the usual limits, in every language:

```sql
CREATE FUNCTION spin_js() RETURNS INT DETERMINISTIC LANGUAGE JAVASCRIPT AS $$ for (;;) {} $$;
CREATE FUNCTION spin_py() RETURNS INT DETERMINISTIC LANGUAGE PYTHON AS $$
    while True:
        pass
$$;
SELECT /*+ MAX_EXECUTION_TIME(1000) */ spin_js();
SELECT /*+ MAX_EXECUTION_TIME(1000) */ spin_py();
```

```text
ERROR 3024 (HY000): Query execution was interrupted, maximum statement execution time exceeded
ERROR 3024 (HY000): Query execution was interrupted, maximum statement execution time exceeded
```

`KILL QUERY` works too. From the shell: one client runs the loop, another kills it after two seconds:

```sh
docker compose exec -T mysql mysql -uapp -papp app -e "SELECT spin_py()" 2>&1 &
sleep 2
id=$(docker compose exec -T mysql mysql -uapp -papp -N \
       -e "SELECT id FROM information_schema.processlist WHERE info = 'SELECT spin_py()'" 2>/dev/null)
docker compose exec -T mysql mysql -uapp -papp -e "KILL QUERY $id" 2>/dev/null
wait
```

```text
ERROR 1317 (70100) at line 1: Query execution was interrupted
```

Memory: each worker has a Java heap limit (`mle.heap_max`) and, where the container may create cgroups
(`privileged: true` in `compose.yaml`), a memory limit for everything the worker does (`mle.memory_max`). A routine
that keeps allocating fails alone:

```sql
CREATE FUNCTION hog() RETURNS INT DETERMINISTIC LANGUAGE JAVASCRIPT AS $$
  const keep = []
  for (;;) keep.push(new Array(1000000).fill(1.5))
$$;
SELECT hog();
```

```text
ERROR 6113 (HY000): JavaScript> Java heap space
```

Even native code that crashes — here numpy reading through a broken view of memory — takes down only the
session's worker; the next call gets a new one:

```sql
CREATE FUNCTION crash() RETURNS DOUBLE DETERMINISTIC LANGUAGE PYTHON AS $$
    import numpy as np
    bad = np.lib.stride_tricks.as_strided(np.zeros(1), shape=(2,), strides=(1 << 46,))
    return float(bad.sum())
$$;
SELECT crash();
SELECT greet('again'), compound(1, 0.5, 1);
```

```text
ERROR 6113 (HY000): MLE> the MLE worker died: killed by signal 6 (Aborted)
+------------------------------------------+---------------------+
| greet('again')                           | compound(1, 0.5, 1) |
+------------------------------------------+---------------------+
| Hello, again! You are 5 characters long. |                1.50 |
+------------------------------------------+---------------------+
```

## 9. Watching MLE

Status variables show the engine's state (your numbers will differ):

<!-- output: varies -->
```sql
SELECT VARIABLE_NAME, VARIABLE_VALUE FROM performance_schema.global_status
 WHERE VARIABLE_NAME IN ('mle_workers', 'mle_pool_idle', 'mle_workers_spawned', 'mle_worker_failures',
                         'mle_oom_errors', 'mle_stored_programs');
```

```text
+---------------------+----------------+
| VARIABLE_NAME       | VARIABLE_VALUE |
+---------------------+----------------+
| mle_oom_errors      | 1              |
| mle_pool_idle       | 2              |
| mle_stored_programs | 21             |
| mle_worker_failures | 1              |
| mle_workers         | 1              |
| mle_workers_spawned | 5              |
+---------------------+----------------+
```

Here: your session holds one worker (`mle_workers`), two more wait in the pool for new sessions, one worker was
lost (the crash in step 8) and one routine ran out of memory (`hog`).

The server's error log (`docker compose logs mysql`) says what happened to workers that were lost. The routines
are ordinary stored routines, with `ROUTINE_BODY` `EXTERNAL` and their language in `EXTERNAL_LANGUAGE`:

```sql
SELECT ROUTINE_NAME, ROUTINE_TYPE, EXTERNAL_LANGUAGE FROM information_schema.ROUTINES
 WHERE ROUTINE_SCHEMA = 'app' AND ROUTINE_NAME IN ('greet', 'word_stats', 'compound', 'place_order');
```

```text
+--------------+--------------+-------------------+
| ROUTINE_NAME | ROUTINE_TYPE | EXTERNAL_LANGUAGE |
+--------------+--------------+-------------------+
| compound     | FUNCTION     | JAVA              |
| greet        | FUNCTION     | JAVASCRIPT        |
| word_stats   | FUNCTION     | PYTHON            |
| place_order  | PROCEDURE    | JAVASCRIPT        |
+--------------+--------------+-------------------+
```

## 10. Clean up

```sh
docker compose down -v 2>/dev/null
```

`-v` also removes the data and package volumes.

**Next:** the user guide (languages and types, the SQL API, libraries, configuration, security, operations).
