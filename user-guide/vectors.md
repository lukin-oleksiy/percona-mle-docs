# Vectors

Percona Server's `VECTOR` type holds embeddings; `DISTANCE` and an HNSW index find the nearest ones. Routines take
over where SQL stops: re-ranking with business data, scores and statistics, "related items" for a whole table, your
own similarity. The [tutorial](../tutorial/index.md#5-vectors-with-python-and-numpy) walks through a complete
example; this page is the reference.

## VECTOR values in each language

| | JavaScript | Python | Java |
|---|---|---|---|
| a `VECTOR` argument | `Float32Array` | numpy `float32` array, read-only | `float[]` |
| a `VECTOR` column of a row | `Float32Array` | numpy `float32` array | `float[]` |
| a `VECTOR` result | `Float32Array`, an array of numbers | numpy array, a list of numbers | `float[]` |
| many rows at once | `fetchColumns()` | `fetch_columns()`: an n × d `float32` matrix | `Vectors.load(sql)` |

A Python `VECTOR` argument is a read-only array that MLE reuses between calls; it is valid during the call. To keep
it or change it, copy it (`v.copy()`).

```sql
CREATE TABLE docs (id BIGINT UNSIGNED PRIMARY KEY, title VARCHAR(40), emb VECTOR(3) NOT NULL);
INSERT INTO docs VALUES
  (1, 'apples',  STRING_TO_VECTOR('[0.9, 0.1, 0.0]')),
  (2, 'pears',   STRING_TO_VECTOR('[0.8, 0.3, 0.1]')),
  (3, 'engines', STRING_TO_VECTOR('[0.0, 0.2, 0.9]')),
  (4, 'trucks',  STRING_TO_VECTOR('[0.1, 0.1, 0.8]'));
CREATE FUNCTION normalized(v VECTOR(3)) RETURNS VECTOR(3) DETERMINISTIC LANGUAGE PYTHON AS $$
    import numpy as np
    return v / np.linalg.norm(v)
$$;
SELECT title, VECTOR_TO_STRING(normalized(emb)) AS unit FROM docs WHERE id = 3;
```

```text
+---------+---------------------------------------+
| title   | unit                                  |
+---------+---------------------------------------+
| engines | [0.00000e+00,2.16930e-01,9.76187e-01] |
+---------+---------------------------------------+
```

## numpy over many rows

`fetch_columns()` turns a query's rows into numpy arrays in one step: a `VECTOR` column becomes one matrix.

```sql
CREATE PROCEDURE centroid_of(IN pattern VARCHAR(20), OUT c TEXT) READS SQL DATA LANGUAGE PYTHON AS $$
    import numpy as np
    cols = session.run_sql('SELECT emb FROM docs WHERE title LIKE ?', [pattern]).fetch_columns()
    m = cols['emb']
    c = '%s rows, centroid %s' % (m.shape[0], [round(float(x), 3) for x in m.mean(axis=0)])
$$;
CALL centroid_of('%s', @c);
SELECT @c;
```

```text
+--------------------------------------+
| @c                                   |
+--------------------------------------+
| 4 rows, centroid [0.45, 0.175, 0.45] |
+--------------------------------------+
```

numpy is also available from JavaScript and Java routines (see [Languages together](polyglot.md#numpy-from-javascript-and-java)).

## The vector helper

`com.percona.mle.api.Vectors` computes similarities and nearest neighbours in Java, with SIMD instructions and, for
large work, several threads (JVM build). It is the same in the three languages: `Java.type('com.percona.mle.api.Vectors')` in
JavaScript, `java.type(...)` in Python (after `import java`), `Vectors` in Java.

| Method | Result |
|---|---|
| `dot(a, b)`, `cosine(a, b)`, `distance(a, b)` | a number (`distance` is Euclidean) |
| `normalize(v)` | a unit vector |
| `load(sql, params...)` | a `VectorSet` of `id, vector` rows, kept in Java |
| `set.nearest(query, k, metric)` | the k nearest rows: `Matches` (`size()`, `id(i)`, `score(i)`) |
| `set.nearestTo(id, k, metric)` | the k nearest to a row of the set, without the row itself |
| `set.related(k, metric)` | for every row, its k nearest (an array of `Matches`) |
| `nearest(query, rows, k, metric)`, `related(rows, k, metric)` | the same for vectors passed in directly |

The metric is `'cosine'`, `'dot'` or `'euclidean'` (in Java also `Vectors.Metric.COSINE`, ...). Scores: similarity
for `cosine` and `dot` (higher is nearer), distance for `euclidean` (lower is nearer).

```sql
CREATE PROCEDURE nearest_titles(IN q VECTOR(3), IN k INT, OUT r TEXT) READS SQL DATA LANGUAGE JAVASCRIPT AS $$
  const Vectors = Java.type('com.percona.mle.api.Vectors')
  const m = Vectors.load('SELECT id, emb FROM docs').nearest(q, k, 'cosine')
  const ids = []
  for (let i = 0; i < m.size(); i++) ids.push(`${m.id(i)} (${m.score(i).toFixed(3)})`)
  r = ids.join(', ')
$$;
CALL nearest_titles(STRING_TO_VECTOR('[1, 0.2, 0]'), 2, @r);
SELECT @r;
```

```text
+----------------------+
| @r                   |
+----------------------+
| 1 (0.996), 2 (0.980) |
+----------------------+
```

## Which tool for which job

| Job | Best done by |
|---|---|
| the nearest rows to one query, in a large table | SQL: `ORDER BY DISTANCE(...) LIMIT k` with an HNSW index |
| re-ranking a few candidates with other columns (price, stock, dates) | a Python routine with `fetch_columns` and numpy |
| related items for every row of a table | the vector helper's `related` (all rows against all) |
| your own similarity or statistics over vectors | numpy (Python, or from JavaScript and Java) |
| a vector function used in a query, row by row | a function in any language (`Vectors.cosine` is fast in all three) |

## Settings

- `mle.vector_threads`: threads the vector helper may use for one call (default 4, at most the number of CPUs; 1:
  no threads).
- `mle.pool_prewarm`: what workers in the pool load before a session needs them; `vectors` (the default) makes the
  vector helper's first use fast, `numpy` imports numpy in advance.
