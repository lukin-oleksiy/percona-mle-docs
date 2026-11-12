# Percona MLE documentation

Documentation of [Percona MLE](https://github.com/lukin-oleksiy/percona-mle): stored functions and procedures in
JavaScript, Python (with numpy) and Java for Percona Server, run by GraalVM in sandboxed worker processes.

| | |
|---|---|
| [Tutorial](tutorial/index.md) | an hour, hands-on, on the Docker image: three languages, SQL from routines, vectors with numpy, libraries across languages, packages, safety, monitoring |
| User guide | (in progress) |
| Architecture & developer guide | (in progress) |

The tutorial is tested: `tutorial/check_tutorial.py` runs every command on the page against the Docker image (with
`percona-mle` checked out next to this repository) and compares every output.

Markdown, laid out for mkdocs (`mkdocs.yml`). A proof of concept; GPLv2 like the code.
