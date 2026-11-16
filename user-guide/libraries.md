# Libraries

A library is code that routines share: functions, classes, constants, a cached model. It is stored in the database
with `CREATE LIBRARY`, in JavaScript, Python or Java, and routines name it in `USING`. Any routine can use a library
of any language (see [Languages together](polyglot.md)).

```sql
CREATE LIBRARY validate LANGUAGE JAVASCRIPT AS $$
  export function email(s) { return /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(s) }
  export function iban(s) {
    const t = s.replace(/\s+/g, '').toUpperCase()
    const moved = (t.slice(4) + t.slice(0, 4)).replace(/[A-Z]/g, c => String(c.charCodeAt(0) - 55))
    let r = 0
    for (const d of moved) r = (r * 10 + Number(d)) % 97
    return r === 1
  }
$$;
CREATE FUNCTION valid_email(s VARCHAR(200)) RETURNS BOOLEAN DETERMINISTIC LANGUAGE JAVASCRIPT
USING (validate) AS $$ return validate.email(s) $$;
CREATE FUNCTION valid_iban(s VARCHAR(40)) RETURNS BOOLEAN DETERMINISTIC LANGUAGE PYTHON
USING (validate) AS $$
    return validate.iban(s)
$$;
SELECT valid_email('ann@example.com') AS email, valid_iban('GB82 WEST 1234 5698 7654 32') AS iban;
```

```text
+-------+------+
| email | iban |
+-------+------+
|     1 |    1 |
+-------+------+
```

## Writing a library

| Language | A library is | Routines see |
|---|---|---|
| JavaScript | an ES module: `export function`, `export const`, `export default` | its exports (`lib.f()`, `lib.default`) |
| Python | a module: code at module level, no common indentation | the module (`lib.f()`, `lib.CONSTANT`) |
| Java | Java source: a `package` and its public classes, with `import` lines | its classes (`lib.Class.method()` from JavaScript and Python; the full class name from Java) |

- **`CREATE LIBRARY` checks the syntax** (Java is compiled), as `CREATE FUNCTION` does.
- **Names:** `schema.name`, or `name` for the current schema; `USING (lib AS alias)` gives it another name in the body.
- **Libraries can use libraries:** `import { f } from '/schema/name'` in JavaScript, `import_library('schema.name')`
  in Python.
- **`information_schema.LIBRARIES`** lists libraries; `ROUTINE_LIBRARIES` shows which routine uses which.

```sql
SELECT LIBRARY_NAME, LANGUAGE FROM information_schema.LIBRARIES WHERE LIBRARY_SCHEMA = 'app';
SELECT ROUTINE_NAME, LIBRARY_NAME FROM information_schema.ROUTINE_LIBRARIES WHERE ROUTINE_SCHEMA = 'app'
 ORDER BY ROUTINE_NAME;
```

```text
+--------------+------------+
| LIBRARY_NAME | LANGUAGE   |
+--------------+------------+
| validate     | JAVASCRIPT |
+--------------+------------+
+--------------+--------------+
| ROUTINE_NAME | LIBRARY_NAME |
+--------------+--------------+
| valid_email  | validate     |
| valid_iban   | validate     |
+--------------+--------------+
```

## Privileges and schemas

- **Creating** a library needs `CREATE ROUTINE` on the schema and the language's privilege
  (`CREATE_JAVASCRIPT_ROUTINE`, ...), as a routine does.
- **Using** one: `USING` names libraries that must exist when the routine is created; whether the routine may import
  them is checked again when it runs.
- **Run-time imports** (`await import('/schema/name')`, `import_library`, `Mle.importLibrary`) reach the libraries of
  the routine's own schema, and those in its `USING`; any other is refused.

## Changing a library

Replace it with `DROP LIBRARY` and `CREATE LIBRARY`. Routines that use it get the new version from their next
statement on; there is no need to recreate them.

```sql
DROP LIBRARY validate;
CREATE LIBRARY validate LANGUAGE JAVASCRIPT AS $$
  export function email(s) { return /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(s) && !s.endsWith('.invalid') }
  export function iban(s) { return false }
$$;
SELECT valid_email('ann@example.invalid') AS email;
```

```text
+-------+
| email |
+-------+
|     0 |
+-------+
```

## State in libraries

A library's module is loaded once per worker and stays loaded, so module-level values survive calls (see
[Concepts](concepts.md#what-lives-how-long)). This is the place for caches: a parsed configuration, a compiled
regular expression, a model read once from a table. Keep data in tables: a worker can be replaced at any time, and
it serves only one account.

## Packages

Third-party packages (npm, PyPI, Maven) are not libraries: the DBA installs them with `mle_package`, and routines
import them by name (`require('x')`, `import x`, a Java class name). See [Packages](packages.md).
