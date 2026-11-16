# Percona MLE documentation

Documentation of [Percona MLE](https://github.com/lukin-oleksiy/percona-mle): stored functions and procedures in
JavaScript, Python (with numpy) and Java for Percona Server, run by GraalVM in sandboxed worker processes.

| | |
|---|---|
| [Tutorial](tutorial/index.md) | an hour, hands-on, on the Docker image: three languages, SQL from routines, vectors with numpy and the vector helper, libraries across languages, packages, safety, monitoring |
| **User guide: writing routines** | [Concepts](user-guide/concepts.md) · [JavaScript](user-guide/javascript.md) · [Python](user-guide/python.md) · [Java](user-guide/java.md) · [Languages together](user-guide/polyglot.md) · [SQL from routines](user-guide/sql.md) · [Libraries](user-guide/libraries.md) · [Vectors](user-guide/vectors.md) · [Debugging](debugging/index.md) |
| **User guide: running MLE** | [Install](install/index.md) · [Configuration and sizing](user-guide/configuration.md) · [Packages](user-guide/packages.md) · [Java libraries](java-libraries/index.md) · [Security](user-guide/security.md) · [Operations](user-guide/operations.md) · [Reference](user-guide/reference.md) · [Limitations](user-guide/limitations.md) |
| [How MLE uses GraalVM's polyglot runtime](architecture/index.md) | one worker process for three languages: what is shared (SQL API, value conversion, the vector helper, libraries, sandbox) and what users get from it |
| **Developer guide** | [Overview and code map](developer-guide/index.md) · [Building](developer-guide/building.md) · [Testing](developer-guide/testing.md) · [A call, end to end](developer-guide/call-path.md) · [Processes and the sandbox](developer-guide/processes.md) · [The wire protocol](developer-guide/protocol.md) · [The worker](developer-guide/worker.md) · [The server side](developer-guide/server-side.md) · [Extending MLE](developer-guide/extending.md) · [Diagnostics](developer-guide/diagnostics.md) |

The pages are tested: `check_docs.py` runs every command on every page against the Docker image (with `percona-mle`
checked out next to this repository) and compares every output. A page that can't run there says why at its top.

Markdown, laid out for mkdocs (`mkdocs.yml`). A proof of concept; GPLv2 like the code.
