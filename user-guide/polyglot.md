# Languages together

In the JVM build, JavaScript, Python and Java run in one worker process, and each language can use the others' code
directly: database libraries, npm and PyPI packages, numpy, Java classes and jars. A call into another language is
an ordinary function call inside the worker (no copy of the call, no extra process), so a routine can take each part
of a task from the language that does it best: numpy for numbers, a Java library for exact or parallel work,
JavaScript for text and JSON.

## What each language can reach

| From \ to | JavaScript library | Python library | Java library | npm package | Python module (numpy, PyPI) | Java class, DBA jar |
|---|---|---|---|---|---|---|
| **JavaScript** | `USING`, `import` | `USING` | `USING` | `require('x')`, `await import('x')` | `importPython('numpy')` | `Java.type('pkg.Class')` |
| **Python** | `USING` | `USING`, `import_library` | `USING` | `import_js('x')` | `import numpy` | `import java`; `java.type('pkg.Class')` |
| **Java** | `Mle.importLibrary` | `Mle.importLibrary` | `USING` (compiled in) | `Mle.importJs("x")` | `Mle.importPython("numpy")` | the class name |

- **`USING`** names the libraries a routine uses, in any language. Each becomes a name in the body: a Python library is
  its module, a JavaScript library its exports, a Java library its classes. In a Java routine, Java libraries are
  compiled with the routine; JavaScript and Python libraries in its `USING` are reached with `Mle.importLibrary`.
- **Run-time imports** name the library with its schema: `import_library('schema.name')`,
  `Mle.importLibrary("schema.name")`, `await import('/schema/name')`. They reach the routine's own schema's libraries
  and those in its `USING`; another schema's library needs `USING`.
- **Any code:** `Polyglot.eval('python', '...')` in JavaScript, `polyglot.eval(language='js', string='...')` in
  Python, `Mle.context().eval("python", "...")` in Java.
- **Routines of other languages** are called through SQL, as any routine: functions in expressions, procedures with
  `CALL` (see below).

## Libraries across languages

A Python library, used from JavaScript and Java:

```sql
CREATE LIBRARY textstats LANGUAGE PYTHON AS $$
import re, statistics

def words(text):
    return re.findall(r"[a-z']+", text.lower())

def summary(text):
    lengths = [len(w) for w in words(text)]
    return {'words': len(lengths), 'mean_length': round(statistics.fmean(lengths), 2)}
$$;
CREATE FUNCTION js_summary(t TEXT) RETURNS JSON LANGUAGE JAVASCRIPT USING (textstats) AS $$
  const s = textstats.summary(t)
  return { words: s.get('words'), mean: s.get('mean_length') }
$$;
CREATE FUNCTION java_words(t TEXT) RETURNS INT LANGUAGE JAVA USING (textstats) AS $$
  return (int) Mle.importLibrary("app.textstats").invokeMember("words", t).getArraySize();
$$;
SELECT js_summary('the quick brown fox') AS from_javascript, java_words('the quick brown fox') AS from_java;
```

```text
+-------------------------+-----------+
| from_javascript         | from_java |
+-------------------------+-----------+
| {"mean": 4, "words": 4} |         4 |
+-------------------------+-----------+
```

A JavaScript library and a Java library, used from Python:

```sql
CREATE LIBRARY fmt LANGUAGE JAVASCRIPT AS $$
  export function money(x, currency) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency }).format(x)
  }
$$;
CREATE LIBRARY exact LANGUAGE JAVA AS $$
package exact;
import java.math.BigDecimal;
import java.math.RoundingMode;
public final class Money {
  public static String split(String total, int parts) {
    return new BigDecimal(total).divide(BigDecimal.valueOf(parts), 2, RoundingMode.HALF_EVEN).toPlainString();
  }
}
$$;
CREATE FUNCTION py_share(total DECIMAL(10,2), parts INT) RETURNS VARCHAR(40) LANGUAGE PYTHON
USING (fmt, exact) AS $$
    share = exact.Money.split(str(total), parts)
    return fmt.money(float(share), 'EUR')
$$;
SELECT py_share(100.00, 3) AS share;
```

```text
+----------+
| share    |
+----------+
| €33.33   |
+----------+
```

## numpy from JavaScript and Java

numpy takes JavaScript and Java arrays as they are, and its results are plain numbers in the calling language:

```sql
CREATE FUNCTION js_stats(xs JSON) RETURNS JSON LANGUAGE JAVASCRIPT AS $$
  const np = importPython('numpy')
  const a = np.array(xs)
  return { mean: a.mean(), std: np.round(a.std(), 3), max: np.max(xs), dtype: a.dtype.name }
$$;
SELECT js_stats('[2, 4, 4, 4, 5, 5, 7, 9]') AS stats;
CREATE FUNCTION java_norm(x DOUBLE, y DOUBLE) RETURNS DOUBLE LANGUAGE JAVA AS $$
  return Mle.importPython("numpy").getMember("linalg").invokeMember("norm", (Object) new double[] {x, y}).asDouble();
$$;
SELECT java_norm(3, 4) AS norm;
```

```text
+---------------------------------------------------+
| stats                                             |
+---------------------------------------------------+
| {"max": 9, "std": 2, "mean": 5, "dtype": "int64"} |
+---------------------------------------------------+
+------+
| norm |
+------+
|    5 |
+------+
```

- **Arrays are copied** into numpy arrays when a numpy function gets them: JavaScript arrays (integers become `int64`,
  other numbers `float64`), typed arrays and Java primitive arrays (their own type: `Float32Array` and `float[]`
  become `float32`), arrays of equal-length rows (2-D), Java `List`s of numbers. The copy is fast (one memory copy
  for numbers); changing the numpy array doesn't change the original.
- **Results:** numpy's numbers and booleans (`numpy.float64`, `numpy.int64`, `numpy.bool_`) are numbers and booleans
  in JavaScript and Java. Arrays stay numpy arrays: call their methods (`a.mean()`, `a.tolist()`, `a.shape`).
- **Arguments of an array's methods** are not converted: pass numpy arrays (`a.dot(np.array(b))`).
- **Other Python modules** imported from JavaScript or Java get Python lists for arrays.
- **In Python code,** `numpy.array`, `asarray` and `asanyarray` also take arrays that came from JavaScript or Java
  (a JavaScript library's result, say).

## Values between languages

Numbers, strings, booleans and `null`/`None` cross as themselves. Other values are shared, not copied: each language
sees the other's object with its own syntax.

| Value | In JavaScript | In Python | In Java |
|---|---|---|---|
| Python `dict` | `d.get('k')`, `d['k']`, `d.k` | | `v.getHashValue("k")` |
| Python `list` | `l[0]`, `l.length` | | `v.getArrayElement(0)`, `getArraySize()` |
| JavaScript object | | `o.k` (attribute) | `v.getMember("k")` |
| JavaScript array | | `a[0]`, `len(a)`, iteration | `v.getArrayElement(0)` |
| Java object | `o.method()`, `o.field` | `o.method()`, `o.field` | |
| Java array, `List` | `a[0]`, `a.length` | `a[0]`, `len(a)` | |

- A value is valid during the call that got it; keep data, not objects, between calls.
- To give another language a plain structure, convert: `JSON.parse(JSON.stringify(x))` in JavaScript,
  `json.loads(json.dumps(x))` in Python, or return JSON from the routine.

## Routines calling routines

Any routine can call a routine of another language through SQL: a function in a query, a procedure with `CALL`. A
JavaScript procedure that uses a Python function and a Java function row by row:

```sql
CREATE TABLE notes (id INT PRIMARY KEY, body TEXT);
INSERT INTO notes VALUES (1, 'MLE runs three languages'), (2, 'Python and Java and JavaScript together');
CREATE PROCEDURE note_report() READS SQL DATA LANGUAGE JAVASCRIPT AS $$
  const r = session.runSql('SELECT id, py_share(100, id + 1) AS share, java_words(body) AS words FROM notes ORDER BY id')
  for (const row of r.fetchAll()) console.log(`note ${row.id}: ${row.words} words, share ${row.share}`)
$$;
CALL note_report();
SELECT mle_session_state('last_output') AS output\G
```

```text
*************************** 1. row ***************************
output: note 1: 4 words, share €50.00
note 2: 6 words, share €33.33
```

## Things to know

- **The JVM build only.** The Python-only native build runs Python alone (with numpy and PyPI packages).
- **One worker per session and account:** all languages of a call run in the same worker, with the same sandbox and
  memory limit (`mle.memory_max` counts all of them).
- **No threads** in Python; in Java, threads must end with the call (see [Java routines](java.md)).
- **Errors cross too.** An exception raised in another language reaches the caller as an exception:
  - JavaScript catches it as usual (`catch (e)`, `e.message` is `ValueError: bad value`);
  - Java catches a `PolyglotException`;
  - Python: errors from JavaScript and Java are `polyglot.ForeignException`, which derives from `BaseException`, not
    `Exception`, so `except Exception` doesn't catch them: use `except polyglot.ForeignException` (after
    `import polyglot`).

  Uncaught, the statement fails with the error and where it happened (see
  [Debugging routines](../debugging/index.md)).
