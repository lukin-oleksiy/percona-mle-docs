# Debugging routines

How to find out why a JavaScript, Python or Java routine fails or returns the wrong value: what `CREATE` checks,
what an error tells you, how to see what a routine printed, and how to try code without creating a routine. The
examples come from the MLE test suite.

| You want to | Use |
|---|---|
| know where an error happened | the error message: `(routine, line N, in function)` |
| see the whole call chain with source lines | `SELECT mle_session_state('traceback')` |
| see what the last call printed | `SELECT mle_session_state('last_output')` |
| see the output with every statement | `SET SESSION mle.console = WARNINGS` |
| try an expression, a library's API, a snippet | `SELECT mle_eval('python', '...')` |
| catch a misspelt name before running | the warnings of `CREATE` (Python) |

## Mistakes found by CREATE

`CREATE FUNCTION` and `CREATE PROCEDURE` compile the body, so a syntax error refuses the routine and names the line.
In each language the message is the language's own:

```text
ERROR 6113 (HY000): JavaScript> SyntaxError: js_bad:1:11 Expected an operand but found ;
ERROR 6113 (HY000): Python> SyntaxError: '(' was never closed (py_bad, line 1)
ERROR 6113 (HY000): Java> broken:1:12: illegal start of expression
```

Line numbers count from the line of `$$`: a body that starts on the line after `$$` has its first statement on
line 2.

A Python body is the body of a function, so the rules for function bodies apply. For example, `from math import *`
is refused (`SyntaxError: import * only allowed at module level`); write `import math` or
`from math import sqrt, pi`.

**Python: names that are never defined.** `CREATE` also looks for names that the body reads but never defines: a
typo such as `lenght` for `len`. They are warnings, and the routine is created:

```sql
CREATE FUNCTION py_typo(word VARCHAR(20)) RETURNS INT LANGUAGE PYTHON AS $$
n = len(word)
return lenght if n > 3 else n
$$;
SHOW WARNINGS;
```

```text
+---------+------+-------------------------------------------------------+
| Level   | Code | Message                                               |
+---------+------+-------------------------------------------------------+
| Warning | 6000 | Python> py_typo, line 3: name 'lenght' is not defined |
+---------+------+-------------------------------------------------------+
```

The check knows the parameters, Python's built-ins, MLE's globals (`session`, `mysql`, ...), the `USING` libraries,
and every name the body assigns, imports or defines anywhere. A name defined only in one branch, or only in another
function, is not reported. Code that uses `exec`, `eval`, `globals()` or `vars()` is not checked: it can define
names the check can't see.

**JavaScript and Java.** Java is compiled at `CREATE`, so an undefined name is an error there
(`cannot find symbol`). JavaScript has no such
check: a misspelt name is a `ReferenceError` when that line runs (routines run in strict mode, so an assignment to an
undeclared name is an error too, not a new global).

## Runtime errors

Three routines that print a line and then fail in a nested helper (Java has no nested functions):

```sql
CREATE FUNCTION py_div(a INT) RETURNS INT LANGUAGE PYTHON AS $$
    def helper(v):
        return 10 // v
    print('before, a =', a)
    return helper(a - a)
$$;
CREATE FUNCTION js_div(a INT) RETURNS INT LANGUAGE JAVASCRIPT AS $$
  function helper(v) { return v.missing.field }
  console.log('before, a =', a)
  return helper({})
$$;
CREATE FUNCTION java_div(a INT) RETURNS INT LANGUAGE JAVA AS $$
  int[] arr = new int[2];
  System.out.println("before, a = " + a);
  return arr[a + 5];
$$;
SELECT py_div(1);
SELECT js_div(1);
SELECT java_div(1);
```

The message names the routine, the line and, for a nested function, its name. It's the innermost line of the
routine's own code:

```text
ERROR 6113 (HY000): Python> ZeroDivisionError: integer division or modulo by zero (py_div, line 3, in helper)
ERROR 6113 (HY000): JavaScript> TypeError: Cannot read property 'field' of undefined (js_div, line 2, in helper)
ERROR 6113 (HY000): Java> java.lang.ArrayIndexOutOfBoundsException: Index 6 out of bounds for length 2 (java_div, line 4)
```

The whole call chain, in the language's usual form and with the source lines, is kept for the session until its
next call:

```sql
SELECT mle_session_state('traceback') AS traceback\G
```

```text
*************************** 1. row ***************************
traceback: Traceback (most recent call last):
  File "py_div", line 5, in <routine>
    return helper(a - a)
  File "py_div", line 3, in helper
    return 10 // v
ZeroDivisionError: integer division or modulo by zero
```

JavaScript and Java list the innermost frame first:

```text
TypeError: Cannot read property 'field' of undefined
    at helper (js_div:2:31)
        function helper(v) { return v.missing.field }
    at js_div:4:10
        return helper({})
```

```text
java.lang.ArrayIndexOutOfBoundsException: Index 6 out of bounds for length 2
    at java_div:4
        return arr[a + 5];
```

Only the routines' frames are shown, not MLE's own. `mle_session_state('stack_trace')` gives the same frames in a
compact form (`<java> java_div:4`), for tools that parse them.

An error that a routine raises on purpose (`throw new Error(...)`, `raise ValueError(...)`) is reported the same way,
so a clear message there is the best help for whoever calls the routine.

## Seeing what a routine printed

`print` in Python, `console.log` in JavaScript and `System.out.println` in Java write to the session's console. Three
ways to read it:

- `mle_session_state('last_output')`: what the last call printed (stdout, then stderr).
- `mle_session_state('stdout')` and `mle_session_state('stderr')`: everything the session's calls printed.
- **`mle.console = WARNINGS`:** what a call prints comes back with the statement as warnings, so you see it without a
  second query. The `mysql` client shows them after each statement with `\W`, `SHOW WARNINGS` lists them, and GUI
  tools show them next to the result.

```sql
CREATE FUNCTION py_talk(n INT) RETURNS INT LANGUAGE PYTHON AS $$
    import sys
    for i in range(n):
        print('step', i, 'square', i * i)
    print('done', file=sys.stderr)
    return n
$$;
SET SESSION mle.console = WARNINGS;
SELECT py_talk(3);
```

```text
+------------+
| py_talk(3) |
+------------+
|          3 |
+------------+
Warning (Code 6000): py_talk: step 0 square 0
Warning (Code 6000): py_talk: step 1 square 1
Warning (Code 6000): py_talk: step 2 square 4
Warning (Code 6000): py_talk stderr: done
```

When a call fails, the warnings carry what it printed before the error, then its traceback:

```text
ERROR 6113 (HY000): Python> ZeroDivisionError: integer division or modulo by zero (py_div, line 3, in helper)
Warning (Code 6000): py_div: before, a = 1
Warning (Code 6000): py_div traceback: Traceback (most recent call last):
Warning (Code 6000): py_div traceback:   File "py_div", line 5, in <routine>
...
```

Notes on `mle.console`:
- It's a session variable, `OFF` by default; `SET GLOBAL` sets it for new sessions. The Docker development image
  sets `WARNINGS`.
- `max_error_count` (1024 by default) limits the warnings of one statement; a call that prints in a loop over many
  rows reaches it quickly. Print a summary instead, or read `last_output`.
- An SQL procedure with a `CONTINUE HANDLER FOR SQLWARNING` sees these warnings too.

Printing is the usual way to look at intermediate values. For rows and dictionaries, print them as JSON
(`json.dumps(d, indent=2, default=str)`, `JSON.stringify(o, null, 2)`) so that they keep their structure.

## Trying code: mle_eval

`mle_eval(language, code)` runs a snippet without creating a routine. It returns what the snippet printed, then the
value of its last expression, as text (`NULL` when there's neither):

```sql
SELECT mle_eval('python', 'import numpy as np\nnp.arange(5) * 2') AS python;
SELECT mle_eval('javascript', 'const a = [1, 2, 3]; a.map(x => x * 2)') AS javascript;
SELECT mle_eval('java', 'Math.max(3, 7)') AS java;
```

```text
+------------------------+
| python                 |
+------------------------+
| array([0, 2, 4, 6, 8]) |
+------------------------+
+------------+
| javascript |
+------------+
| [2,4,6]    |
+------------+
+------+
| java |
+------+
| 7    |
+------+
```

- The language is `javascript`, `python` or `java` (any case). Python and Java show values as their `repr` and
  `toString`; JavaScript shows arrays and objects as JSON.
- In Java the code is either an expression or statements (`int s = 0; for (...) s += i; System.out.println(s);`).
- In SQL string literals, `\n` is a new line, as above.
- Errors are reported as in routines, with `mle_eval` as the routine's name:
  `Python> ZeroDivisionError: division by zero (mle_eval, line 3)`.

Limits:
- **No SQL.** `session.run_sql(...)` and the other SQL methods are refused (`mle_eval cannot run SQL: put the code in
  a procedure to run SQL`): `mle_eval` is a function called inside a statement. To try a query, put it in a
  procedure.
- **No state between calls.** Each call starts afresh, like a routine's call: a variable set in one call is not
  there in the next. Imports are cached by the worker, so importing a large library again is fast.
- It needs the language's privilege to create routines (`CREATE_PYTHON_ROUTINE`, `CREATE_JAVASCRIPT_ROUTINE`,
  `CREATE_JAVA_ROUTINE`): the same code could be put in a routine. It runs in the session's worker, in the same
  sandbox and under the same memory limits as routines, and `KILL` stops it.

## Common mistakes

| Symptom | Cause |
|---|---|
| Python: `IndentationError` at `CREATE` | the body's lines are indented differently: indent them all alike, or not at all |
| JavaScript: `ReferenceError: x is not defined` | a misspelt name, or an assignment without `let`/`const` (strict mode) |
| A result of `NULL` where a value was expected | a JavaScript function that ends without `return`; a Python body without `return` |
| `Java> ... cannot find symbol` at `CREATE` | a class without its package: bodies have no `import` lines, so write `java.util.Map` (common `java.util`, `java.math` and `java.time` classes are already imported) |
| a call that is slow the first time | the worker compiles the routine on its first call in a session; later calls are fast |

## What is not there yet

There is no step-by-step debugger: breakpoints and variable inspection in the routine's source are planned after the
proof of concept. Until then, print values (with `mle.console = WARNINGS`) and try pieces with `mle_eval`.
