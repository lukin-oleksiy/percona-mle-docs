# Statistics

MySQL has no SQL for percentiles, the median, correlation or regression (`PERCENTILE_CONT`, `MEDIAN`, `CORR` and
`REGR_*` are missing). The statistics helper `com.percona.mle.api.Stats` computes them in Java, the same from the
three languages: `Java.type('com.percona.mle.api.Stats')` in JavaScript, `java.type(...)` in Python (after
`import java`), `Stats` in Java. Its formulas follow numpy, pandas and scipy, and the tests compare with their
results. What SQL does well (sums, averages, moving averages, ranks, top N per group) stays in SQL.

## Columns

A column is a `double[]` in Java; JavaScript and Python pass a numpy array, a `Float64Array`, an array or a list. A
missing value (SQL `NULL`, `None`, `null`, `NaN`) is left out, as pandas does; a pair with a missing value on either
side is left out of correlations and regressions.

| Method | Result |
|---|---|
| `describe(x)` | a `Summary`: `count()`, `missing()`, `mean()`, `std()` (sample), `min()`, `q1()`, `median()`, `q3()`, `max()`, `iqr()`, `skewness()`, `kurtosis()` (as pandas' `describe()`, `skew()`, `kurt()`) |
| `quantile(x, q)`, `quantiles(x, [q, ...])`, `median(x)`, `iqr(x)` | by linear interpolation, numpy's and pandas' default |
| `mode(x)` | the most frequent value; a tie goes to the smallest |
| `histogram(x, bins)`, `histogramEdges(x, edges)` | a `Histogram` (`edges()`, `counts()`), as numpy's: the last bin includes its right edge |
| `zscore(x)` | (x − mean) / std, with the population std as `scipy.stats.zscore` |
| `outliers(x, 'iqr', k)`, `outliers(x, 'zscore', k)` | the positions of values beyond q1 − k·IQR or q3 + k·IQR (k 1.5 is usual), or with abs(z) above k |
| `rank(x)` | ranks from 1, ties get their average rank |
| `correlation(x, y, 'pearson' or 'spearman')`, `covariance(x, y)` | for the pairs without a missing value |
| `correlationMatrix([x, y, ...], method)` | every pair of columns |
| `linregress(x, y)` | a `Regression`: `slope()`, `intercept()`, `r()`, `r2()`, `pValue()` (two-sided, slope = 0), `stderr()`, `interceptStderr()`, `n()`, `predict(x)` (a number or a column), as `scipy.stats.linregress` |

Results print as JSON: `String(s)` in JavaScript, `str(s)` in Python.

## From SQL, per group

`Stats.load(sql, params...)` runs a query and keeps its columns in Java as a `Frame`: numbers as numbers, anything else
as text. A `Frame` has the methods above by column name (`t.describe('price')`, `t.correlation('price', 'qty',
'pearson')`, `t.linregress('qty', 'price')`, `t.outliers('price', 'iqr', 1.5)`) and:

| Method | Result |
|---|---|
| `t.groupBy(column)` | the rows by the column's values (`NULL` is a group), in ascending order |
| `.median(c)`, `.quantile(c, q)`, `.mean(c)`, `.count()`, `.describe(c)`, `.correlation(a, b, method)` | a `Frame`: the key, then the statistic |
| `t.correlationMatrix(method, a, b, ...)` | a `Frame`: one row per column |
| `t.select(positions)` | the rows at these positions (e.g. the outliers) |
| `t.column(c)`, `t.text(c)`, `t.rows()`, `t.size()`, `t.columns()` | the data back: a column, rows for `executeMany` |

Like `Vectors.load`, `Stats.load` runs SQL, so it works in procedures, not in stored functions.

```sql
CREATE TABLE sales (id INT PRIMARY KEY, region VARCHAR(8), price DOUBLE, qty INT);
INSERT INTO sales VALUES (1, 'n', 10, 1), (2, 's', 12.5, 2), (3, 'n', 11, 3), (4, 'e', 30, 4), (5, 's', 9, 5),
  (6, 'n', NULL, 6), (7, 'e', 31, 7), (8, 's', 14, 8), (9, 'n', 13.5, 9), (10, NULL, 7, 10);
CREATE PROCEDURE price_medians(OUT r TEXT) READS SQL DATA LANGUAGE PYTHON AS $$
    import java
    Stats = java.type('com.percona.mle.api.Stats')
    r = str(Stats.load('SELECT region, price FROM sales').groupBy('region').median('price'))
$$;
CALL price_medians(@r);
SELECT @r;
```

```text
+------------------------------------------------------------------------------------------------------------------------------+
| @r                                                                                                                           |
+------------------------------------------------------------------------------------------------------------------------------+
| [{"region": null, "price": 7}, {"region": "e", "price": 30.5}, {"region": "n", "price": 11}, {"region": "s", "price": 12.5}] |
+------------------------------------------------------------------------------------------------------------------------------+
```

The same on columns passed in, here from JavaScript:

```sql
CREATE FUNCTION quick_stats() RETURNS TEXT LANGUAGE JAVASCRIPT AS $$
  const S = Java.type('com.percona.mle.api.Stats')
  const x = [4, 1, null, 3, 2, 50]
  const lr = S.linregress([1, 2, 3, 4], [2, 4.1, 5.9, 8.2])
  return `median ${S.median(x)}, iqr ${S.iqr(x)}, slope ${lr.slope().toFixed(2)} ` +
         `(p ${lr.pValue().toPrecision(2)}), outliers [${Array.from(S.outliers(x, 'iqr', 1.5))}]`
$$;
SELECT quick_stats() AS r;
```

```text
+------------------------------------------------------+
| r                                                    |
+------------------------------------------------------+
| median 3, iqr 2, slope 2.04 (p 0.0010), outliers [5] |
+------------------------------------------------------+
```
