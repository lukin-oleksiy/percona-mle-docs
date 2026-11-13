# Install: builds and packages

Percona MLE comes in two builds. Both run routines in sandboxed worker processes outside the server; they differ
in what runs inside a worker.

| | JVM build | Python-only native build |
|---|---|---|
| Languages | Python (with numpy), Java, JavaScript | Python (with numpy) |
| Database libraries | in all three languages, usable across languages | in Python |
| Third-party packages | PyPI, Maven, npm (installed by the DBA) | PyPI (installed by the DBA; `mle_package` itself needs the JVM build, e.g. on another host or in the tools image) |
| Worker | a JVM with GraalPy, GraalJS and Java | a GraalVM Native Image: no JVM, no JDK |
| Memory per worker | a few hundred MB | about half |
| A new worker's first call | about 2.5 s | about 0.25 s |
| Installed size | about 650 MB (with its AOT cache) | about 450 MB |

The Python-only native build is highly optimised for Python-only use, such as vector processing with numpy next to
Percona Server's `VECTOR` columns. Choose the JVM build when routines need Java or JavaScript, or libraries shared
across languages.

## Packages

Packages are built from the [percona-mle](https://github.com/lukin-oleksiy/percona-mle) sources (`cpack -G DEB` or
`cpack -G RPM` in the build directory; see its README). One package per part:

| Package | Contents | JVM build | Python-only build |
|---|---|---|---|
| `percona-mle-common` | `component_mle`, the launcher and the worker bootstrap | yes | yes |
| `percona-mle` | the JVM build: worker, runtime jars, JavaScript and Java | yes | |
| `percona-mle-jdk` | a trimmed GraalVM runtime | yes | |
| `percona-mle-python-runtime` | Python's standard library and the numpy environment | with Python | yes |
| `percona-mle-python` | GraalPy for the JVM build | with Python | |
| `percona-mle-python-native` | the Python-only native build | | yes |
| `percona-mle-tools` | `mle_package`, the DBA's tool for third-party packages (runs on the JVM build) | optional | |

On Debian or Ubuntu, for example:

```sh
apt install percona-mle percona-mle-python        # the JVM build with Python (pulls common, jdk, python-runtime)
apt install percona-mle-python-native             # the Python-only native build (pulls common, python-runtime)
```

The `percona-mle` package makes the JDK's AOT cache that JVM workers start from when it is installed, and again when
the JDK or Python packages change.

## Choosing the build: `mle.worker`

`mle.worker` (read-only, set in `my.cnf`) selects the build that runs routines:

| Value | Meaning |
|---|---|
| `AUTO` (default) | the JVM build when it is installed (`percona-mle`), else the Python-only native build |
| `JVM` | the JVM build |
| `NATIVE` | the Python-only native build |

With the native build, `mle_languages_supported` shows `Python`, and `CREATE FUNCTION ... LANGUAGE JAVA` or
`LANGUAGE JAVASCRIPT` (and libraries in those languages) are refused as unsupported languages.

## Enabling MLE in the server

```sql
INSTALL COMPONENT 'file://component_lang_router';
INSTALL COMPONENT 'file://component_mle';
GRANT CREATE_PYTHON_ROUTINE ON *.* TO 'app'@'%';   -- and CREATE_JAVASCRIPT_ROUTINE, CREATE_JAVA_ROUTINE (JVM build)
```

## Starting Python ahead: `mle.pool_prewarm`

Workers wait in a pool for new sessions. By default (`none`) a worker starts Python on its first Python routine,
which keeps idle workers small but makes that first call slower (about two seconds on the JVM build, a fraction of
a second on the native build). `python` starts Python in pool workers in advance, and `numpy` also imports numpy:
more memory per waiting worker, fast first calls.

## Requirements

- Linux on x86-64, kernel 6.12 or later (Landlock ABI 6: the sandbox needs it).
- cgroup v2; for memory and process limits per worker, a cgroup delegated to mysqld (systemd: `Delegate=yes`).
  Without it MLE runs, with the workers' own heap limit and the system's limits.
- Percona Server with the language router (`component_lang_router`), of the server line the packages were built
  for.
