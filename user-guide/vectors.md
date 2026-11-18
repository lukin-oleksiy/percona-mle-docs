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

## More jobs on vectors

What people most often do with numpy on embeddings, in Java (SIMD, threads) and the same in the three languages. On a
`VectorSet` (rows kept in Java) and, for rows passed in, as `Vectors` methods with the rows first.

| Method | Result |
|---|---|
| `set.mmr(query, k, lambda[, fetchK])` | a diverse top k (maximal marginal relevance, for RAG): the `fetchK` (default 4k) nearest by cosine, then k of them picked for relevance and against repeating each other; `lambda` 1 is plain relevance, 0 maximal diversity, 0.5–0.8 is usual |
| `set.within(query, threshold, metric)` | every row at least `threshold` similar (cosine, dot) or at most `threshold` away (euclidean), best first |
| `set.pairs(threshold, metric[, maxPairs])` | `Pairs` of similar rows, best first (`first(i)`, `second(i)`, `firstId(i)`, `secondId(i)`, `score(i)`): near duplicates, paraphrases; at most 100,000 by default |
| `set.groups(threshold, metric[, minSize])` | `Groups` of rows linked by close pairs (`members(g)`, `ids(g)`); single linkage, so keep the threshold high |
| `set.dedup(threshold, metric)` | the ids to keep: the first row of each group and every row without a near duplicate |
| `set.mean()`, `set.meanOf(ids[, weights])` | a centroid; a profile from the items a user liked |
| `set.kmeans(k, metric[, maxIterations, seed])` | `Clusters` (`cluster(i)`, `sizes()`, `members(c)`, `ids(c)`, `centroid(c)`, `score(i)`, `assign(v)`, `inertia()`): `euclidean`, or `cosine` for spherical k-means; the same seed gives the same clusters |
| `set.classify(query, k, metric)` | the label most common among the k nearest (labels: a third column, `SELECT id, vector, label`) |
| `set.truncate(dims)` | a set of the first `dims` dimensions, normalized again (Matryoshka embeddings) |
| `pairwise(a, b, metric)`, `similarity(a, b, metric)` | row i with row i; every row with every row (at most 25 million scores) |
| `mean(rows[, weights])`, `sum(rows)`, `add`, `subtract`, `scale`, `norm`, `manhattan`, `truncate(v, dims)`, `normalizeAll(rows)` | vector arithmetic |
| `Matches.of(ids, scores[, distance])` | a ranked list from anywhere, for example a full-text search |
| `Matches.fuse(k, [m1, m2, ...])`, `Matches.blend([m1, m2], [w1, w2])` | hybrid search: reciprocal rank fusion (k = 60 is usual), or a weighted sum of scores scaled to [0, 1] |

```sql
CREATE PROCEDURE hybrid(IN words TEXT, IN q VECTOR(384), OUT r TEXT) READS SQL DATA LANGUAGE JAVASCRIPT AS $$
  const V = Java.type('com.percona.mle.api.Vectors')
  const M = Java.type('com.percona.mle.api.Matches')
  const text = session.runSql('SELECT id, MATCH(body) AGAINST (?) AS s FROM docs WHERE MATCH(body) AGAINST (?) ' +
                              'ORDER BY s DESC LIMIT 20', [words, words]).fetchAll()
  const byText = M.of(text.map(x => x.id), text.map(x => x.s))
  const byVector = V.load('SELECT id, emb FROM docs').mmr(q, 20, 0.7)
  r = String(M.fuse(60, [byText, byVector]))
$$;
```

## Which tool for which job

| Job | Best done by |
|---|---|
| the nearest rows to one query, in a large table | SQL: `ORDER BY DISTANCE(...) LIMIT k` with an HNSW index |
| re-ranking a few candidates with other columns (price, stock, dates) | a Python routine with `fetch_columns` and numpy |
| related items for every row of a table | the vector helper's `related` (all rows against all) |
| diverse results, near duplicates, clusters, hybrid ranking | the vector helper (above) |
| your own similarity or statistics over vectors | numpy (Python, or from JavaScript and Java) |
| a vector function used in a query, row by row | a function in any language (`Vectors.cosine` is fast in all three) |

## Settings

- `mle.vector_threads`: threads the vector helper may use for one call (default 4, at most the number of CPUs; 1:
  no threads).
- `mle.pool_prewarm`: what workers in the pool load before a session needs them; `vectors` (the default) makes the
  vector helper's first use fast, `numpy` imports numpy in advance.
