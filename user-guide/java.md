# Java routines

A Java routine's body is the body of a static method: statements, `return` for a function's result. It is compiled
by `javac` when the routine is created, so type errors and unknown names are reported by `CREATE`. Java needs the
JVM build of MLE (Java 25).

```sql
CREATE FUNCTION loan_payment(principal DECIMAL(12,2), yearly_rate DECIMAL(6,4), months INT)
RETURNS DECIMAL(12,2) DETERMINISTIC LANGUAGE JAVA AS $$
  MathContext mc = MathContext.DECIMAL64;
  BigDecimal r = yearly_rate.divide(BigDecimal.valueOf(12), mc);
  BigDecimal f = BigDecimal.ONE.add(r).pow(months, mc);
  return principal.multiply(r).multiply(f).divide(f.subtract(BigDecimal.ONE), 2, RoundingMode.HALF_EVEN);
$$;
SELECT loan_payment(10000, 0.05, 36) AS monthly;
```

```text
+---------+
| monthly |
+---------+
|  299.71 |
+---------+
```

## The body

- **Imports:** `java.math.*`, `java.time.*`, `java.util.*` and MLE's API (`com.percona.mle.api.*`,
  `com.percona.mle.sql.*`) are imported; other classes by their full name (`java.util.regex.Pattern`), or in a
  class of your own (below).
- **Parameters** are variables of the types below, boxed so that `NULL` is `null`.
- **`return`** gives the result; `return null` is `NULL`.
- **Errors at `CREATE`:** `javac`'s messages, with the line:

```sql
CREATE FUNCTION typo(a INT) RETURNS INT LANGUAGE JAVA AS $$
  int total = a + 1;
  return totl;
$$;
```

```text
ERROR 6113 (HY000): Java> typo:3:10: cannot find symbol
  symbol:   variable totl
  location: routine typo
```

**A class of your own.** If the body declares a public class, MLE calls its public static method named like the
routine (or its only public static method). The class can have `import` lines, fields, other methods and nested
classes:

```sql
CREATE FUNCTION vowels(s VARCHAR(200)) RETURNS INT DETERMINISTIC LANGUAGE JAVA AS $$
import java.util.regex.Pattern;

public class Vowels {
  private static final Pattern VOWEL = Pattern.compile("[aeiouy]", Pattern.CASE_INSENSITIVE);

  public static Integer vowels(String s) {
    return s == null ? null : (int) VOWEL.matcher(s).results().count();
  }
}
$$;
SELECT vowels('Percona Server') AS vowels;
```

```text
+--------+
| vowels |
+--------+
|      5 |
+--------+
```

## Parameters and results

| SQL type | Java type |
|---|---|
| `TINYINT`, `SMALLINT`, `MEDIUMINT`, `INT`, `YEAR` | `Integer` |
| `INT UNSIGNED`, `BIGINT`, `BIT` | `Long` |
| `BIGINT UNSIGNED` | `java.math.BigInteger` |
| `FLOAT` | `Float` |
| `DOUBLE` | `Double` |
| `DECIMAL` | `java.math.BigDecimal` |
| `CHAR`, `VARCHAR`, `TEXT`, `ENUM`, `SET`, `JSON` | `String` |
| `BINARY`, `VARBINARY`, `BLOB` | `byte[]` |
| `DATE` | `java.time.LocalDate` |
| `DATETIME`, `TIMESTAMP` | `java.time.LocalDateTime` |
| `TIME` | `java.time.Duration` |
| `VECTOR` | `float[]` |

The result has the type of `RETURNS` from the same table.

## Procedures: OUT and INOUT parameters

`OUT` and `INOUT` parameters are one-element arrays: assign `p[0]`. `OUT` parameters start as `null`.

```sql
CREATE PROCEDURE stats(IN xs JSON, OUT n INT, OUT mean DOUBLE) LANGUAGE JAVA AS $$
  String[] parts = xs.replaceAll("[\\[\\] ]", "").split(",");
  double sum = 0;
  for (String p : parts) sum += Double.parseDouble(p);
  n[0] = parts.length;
  mean[0] = sum / parts.length;
$$;
CALL stats('[1, 2, 3, 6]', @n, @mean);
SELECT @n, @mean;
```

```text
+------+-------+
| @n   | @mean |
+------+-------+
|    4 |     3 |
+------+-------+
```

## SQL and errors

`Mle.session()` is the caller's session, with the same API as in the other languages, in Java's types (procedures
only; see [SQL from routines](sql.md)):

```sql
CREATE TABLE prices (item VARCHAR(20) PRIMARY KEY, price DECIMAL(8,2));
INSERT INTO prices VALUES ('tea', 3.50), ('coffee', 4.20), ('cake', 5.00);
CREATE PROCEDURE price_list(OUT list TEXT) READS SQL DATA LANGUAGE JAVA AS $$
  StringBuilder b = new StringBuilder();
  for (Row r : Mle.session().runSql("SELECT item, price FROM prices WHERE price < ? ORDER BY item", 5).fetchAll())
    b.append(r.getField("item")).append('=').append(r.getField("price")).append(' ');
  list[0] = b.toString().trim();
$$;
CALL price_list(@list);
SELECT @list;
```

```text
+----------------------+
| @list                |
+----------------------+
| coffee=4.20 tea=3.50 |
+----------------------+
```

- An exception that leaves the body fails the statement with error 6113 and where it happened.
- `throw new MleSqlException(errorNumber, sqlState, message)` raises an SQL error of your own, as SQL's `SIGNAL`.
- A failing SQL statement throws an `MleSqlException` (`getErrorCode()`, `getSqlState()`, `getMessage()`).

```sql
CREATE PROCEDURE check_price(IN p DECIMAL(8,2)) LANGUAGE JAVA AS $$
  if (p.signum() < 0) throw new MleSqlException(5002, "45002", "a price must not be negative");
$$;
CALL check_price(-1);
```

```text
ERROR 5002 (45002): a price must not be negative
```

## Libraries, jars and the other languages

- **Java libraries:** `CREATE LIBRARY ... LANGUAGE JAVA` holds Java source (a package, its classes); a routine names
  it in `USING` and uses its classes by their full name. The library is compiled with the routine.
- **The DBA's jars** (from Maven, with `mle_package`): their classes by full name. See
  [Java libraries](../java-libraries/index.md).
- **JavaScript, Python, numpy:** `Mle.importPython("numpy")`, `Mle.importJs("package")`,
  `Mle.importLibrary("schema.library")` for a JavaScript or Python library, `Mle.context()` for any code. See
  [Languages together](polyglot.md).

```sql
CREATE LIBRARY units LANGUAGE JAVA AS $$
package units;
public final class Length {
  private Length() {}
  public static double toMiles(double km) { return km / 1.609344; }
}
$$;
CREATE FUNCTION miles(km DOUBLE) RETURNS DOUBLE DETERMINISTIC LANGUAGE JAVA USING (units)
AS $$ return Math.round(units.Length.toMiles(km) * 10) / 10.0; $$;
SELECT miles(42.195) AS marathon_miles;
```

```text
+----------------+
| marathon_miles |
+----------------+
|           26.2 |
+----------------+
```

## Threads, static fields, warm-up

- **Threads:** a routine may work in parallel (parallel streams, virtual threads, an executor it closes), as long
  as every thread it started has ended when the call returns. A thread left running retires the worker after the
  session.
- **Static fields** live as long as the worker: they are kept between calls, and can be seen by later sessions of
  the same account (with `SQL SECURITY DEFINER`, by everyone who calls the routine). Use them for caches, not for
  data.
- **Warm-up:** a routine is compiled once per worker; the first calls run before the JIT compiler has optimised
  them, later ones are fast.
