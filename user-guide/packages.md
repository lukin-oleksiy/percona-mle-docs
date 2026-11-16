# Packages

Routines can't download or install anything. Third-party packages from npm, PyPI and Maven are installed by the DBA
with `mle_package`, into a directory that mysqld and its workers only read (`mle.package_dir`). Every routine can
then use them, in any language (see [Languages together](polyglot.md)).

<!-- check-page: skip: installs packages from the network with the tools image; the tutorial (step 7) runs this -->

## Installing

`mle_package` runs on the database host (package `percona-mle-tools`) or in the tools Docker image, as an OS user who
may write the package directory:

```sh
mle_package install js     lodash@4.17.21 date-fns
mle_package install python networkx
mle_package install java   org.apache.commons:commons-math3:3.6.1
mle_package list
mle_package remove python networkx
```

- **npm** packages go to `js/node_modules`; **PyPI** packages to `python/site-packages`, installed with GraalPy
  (GraalPy's binary wheels are used where they exist, e.g. for numpy-based packages); **Maven** artifacts and their
  dependencies to `java/` as jars.
- `packages.json` in the directory lists what is installed; `SELECT mle_packages()` shows it from SQL.
- The directory must not be writable by the group or others: workers run what is in it.

## Making new packages visible

Workers load the package directory when they start, and a running worker keeps what it had. After installing:

```sql
SELECT mle_packages_refresh();
```

- It needs `SYSTEM_VARIABLES_ADMIN`, and is refused while sessions hold workers (a session can release its own with
  `SELECT mle_session_reset();`).
- MLE then starts new workers (the pool too); they see the change.

## Using packages

| Package | JavaScript | Python | Java |
|---|---|---|---|
| npm | `require('lodash')`, `await import('date-fns')` | `import_js('lodash')` | `Mle.importJs("lodash")` |
| PyPI | `importPython('networkx')` | `import networkx` | `Mle.importPython("networkx")` |
| Maven jar | `Java.type('org.apache.commons.math3.util.FastMath')` | `java.type(...)` after `import java` | the class name |

The [tutorial](../tutorial/index.md#7-third-party-packages-the-dba) installs `networkx` and uses it from a Python
function; [Java libraries](../java-libraries/index.md) is the DBA's guide to jars (choosing a library, the rules a
library must follow in a worker).

## What to check before installing

- **Code you install runs in every worker** of every account that uses it, inside the sandbox: it can't reach files
  or the network, but it can read whatever data the routines that call it pass to it.
- **Native code:** Python packages with C extensions need a build for GraalPy: `mle_package` uses GraalPy's binary
  wheels where they exist, else pip builds the package from source (which needs a compiler on the host).
- **Threads:** a Java library that keeps threads running after a call (thread pools, timers) makes MLE retire the
  worker after each session. See [Java libraries](../java-libraries/index.md#rules-for-libraries-in-mle-workers).
- **The native build** has Python only: npm packages and jars need the JVM build.
