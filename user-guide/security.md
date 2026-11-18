# Security

Code in JavaScript, Python and Java can do far more than SQL: compute, parse, load native libraries. MLE's model is
that such code never runs inside mysqld, runs only for the account it belongs to, and can reach only what the
account could reach with SQL.

## Who may do what

| Action | Needs |
|---|---|
| create a routine or library in a language | `CREATE ROUTINE` and `CREATE_JAVASCRIPT_ROUTINE`, `CREATE_PYTHON_ROUTINE` or `CREATE_JAVA_ROUTINE` |
| try code with `mle_eval(language, code)` | the language's privilege, as above |
| call a routine | `EXECUTE` |
| install third-party packages | the OS user who may write `mle.package_dir` (`mle_package`), then `SYSTEM_VARIABLES_ADMIN` for `mle_packages_refresh()` |
| change limits and languages | the `mle.*` system variables (`SYSTEM_VARIABLES_ADMIN`) |

- Grant the language privileges like `CREATE ROUTINE` on a schema: to the accounts that write code, not to
  applications.
- `mle.languages` turns languages off for the whole server.

## What a routine runs as

- **The routine's account:** its definer (`SQL SECURITY DEFINER`, the default) or the caller (`SQL SECURITY
  INVOKER`). SQL statements run with that account's privileges, in the caller's session and transaction, as for an
  SQL routine.
- **One worker per session and account.** A session that calls routines of two definers has two workers. Code of
  one account never runs in a process with another account's code or data.
- **`SQL SECURITY INVOKER` routines of another definer get a worker of their own** (per caller and definer). They
  run with the caller's privileges, as in MySQL, but what their code changes inside the language (JavaScript's
  built-in objects, Python's `builtins`, Java static fields) stays away from the caller's own routines. A session
  that calls `INVOKER` routines of several definers holds a worker for each. Libraries are different: a routine
  that imports a library (`USING`, `import`) runs the library's code in its own worker, as its own code. Code
  that such a routine evaluates with `mle_eval()` runs in the routine's worker too. (An SQL-language `INVOKER`
  function of another definer that calls `mle_eval()` is not covered: it runs the snippet in the caller's
  worker, if the caller may create routines in that language.)
- **Source lines in tracebacks** (`mle_session_state('traceback')`, `mle.console = WARNINGS`) are shown to those
  who may see the routine's definition: its definer and accounts with `SHOW_ROUTINE`. Other callers get the
  frames (routine, line, function) without the source text.
- **Workers are reused only for the same account:** after a session, a worker may be kept for the account's next
  session (`mle.worker_cache_size`). Values a routine leaves in library state, Python modules or Java static fields
  can then be seen by that account's later calls. With `SQL SECURITY DEFINER`, the account is the definer, so
  different callers of the same routine can share a worker over time: don't keep one caller's data in such state.

## The sandbox

Each worker is a separate process, started by `mle_launcher` and locked down by the operating system before it runs
any code:

- **Files:** it can read only what it needs (MLE's files, the package directory, system libraries) and write
  nowhere. mysqld's data directory and plugin directory are hidden.
- **Network and processes:** no sockets, no new processes or programs, no access to other processes' memory, no
  signals to other processes (Landlock and a seccomp filter).
- **Memory:** a Java heap limit (`mle.heap_max`) and, with a delegated cgroup, a limit on everything the worker uses
  (`mle.memory_max`). Without a cgroup, the kernel kills a worker before mysqld when memory runs out.
- **Time:** `KILL QUERY`, `KILL` and `MAX_EXECUTION_TIME` stop a routine in any language; a worker that doesn't stop
  in time (`mle.interrupt_grace_ms`) is killed.
- **Threads:** Python routines can't start threads; Java threads must end with the call, or the worker is retired.
- **On RHEL and compatible systems** SELinux confines the launcher and the workers in domains of their own (see
  [Install](../install/index.md)).

```sql
CREATE FUNCTION try_files() RETURNS TEXT LANGUAGE PYTHON AS $$
    import os
    out = []
    for path in ('/etc/passwd', '/var/lib/mysql'):
        try:
            os.listdir(path) if path.endswith('mysql') else open(path).read()
            out.append(path + ': readable')
        except OSError as e:
            out.append('%s: %s' % (path, e.strerror))
    return '; '.join(out)
$$;
SELECT try_files();
CREATE FUNCTION try_network() RETURNS TEXT LANGUAGE JAVA AS $$
  try (java.net.Socket s = new java.net.Socket("127.0.0.1", 3306)) {
    return "connected";
  } catch (Exception e) {
    return e.getClass().getSimpleName() + ": " + e.getMessage();
  }
$$;
SELECT try_network();
```

```text
+-------------------------------------------------------------------+
| try_files()                                                       |
+-------------------------------------------------------------------+
| /etc/passwd: Permission denied; /var/lib/mysql: Permission denied |
+-------------------------------------------------------------------+
+------------------------------------------+
| try_network()                            |
+------------------------------------------+
| SocketException: Operation not permitted |
+------------------------------------------+
```

## When a worker fails

A crash in native code (a C extension), running out of memory or a killed worker ends only that worker: the call
fails (error 6000, `MLE> the worker died: ...`), mysqld and other sessions go on, and the session's next call gets a
new worker. `mle_worker_failures` counts these.

## Third-party code

Packages the DBA installs run in every worker that imports them, inside the same sandbox: they can't reach files or
the network, but they see the data routines pass them. Install what you would install on an application server, from
sources you trust (see [Packages](packages.md)).
