# Percona MLE documentation

Documentation of [Percona MLE](https://github.com/lukin-oleksiy/percona-mle): stored functions and procedures in
JavaScript, Python (with numpy) and Java for Percona Server, run by GraalVM in sandboxed worker processes.

| | |
|---|---|
| [Tutorial](tutorial/index.md) | an hour, hands-on, on the Docker image: three languages, SQL from routines, vectors with numpy and the vector helper, libraries across languages, packages, safety, monitoring |
| [Debugging routines](debugging/index.md) | errors with their location, the traceback, what a call printed (`mle.console = WARNINGS`), trying code with `mle_eval`, the checks of `CREATE`, common mistakes |
| [Install: builds and packages](install/index.md) | the JVM build and the Python-only native build (highly optimised for Python-only use), the packages, `mle.worker`, `mle.pool_prewarm`, requirements |
| [Java libraries for routines](java-libraries/index.md) | a DBA guide: installing jars, using them from Java, Python and JavaScript, choosing a library for numeric work, the rules a library must follow in MLE workers |
| User guide | (in progress) |
| [How MLE uses GraalVM's polyglot runtime](architecture/index.md) | one worker process for three languages: what is shared (SQL API, value conversion, the vector helper, libraries, sandbox) and what users get from it |
| Developer guide | (in progress) |

The tutorial is tested: `tutorial/check_tutorial.py` runs every command on the page against the Docker image (with
`percona-mle` checked out next to this repository) and compares every output.

Markdown, laid out for mkdocs (`mkdocs.yml`). A proof of concept; GPLv2 like the code.
