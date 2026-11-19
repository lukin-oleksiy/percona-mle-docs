<!-- check-page: skip: installs MLE on a server; the Docker image has it installed -->
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
| `percona-mle-apparmor` | the AppArmor profile for MLE's processes (DEB only; recommended by `percona-mle-common`, so `apt` installs it with it) | Debian, Ubuntu | Debian, Ubuntu |
| `percona-mle-selinux` | the SELinux policy for MLE's processes (RPM only; installed with `percona-mle-common` where SELinux policy is installed) | RHEL | RHEL |

On Debian or Ubuntu, for example:

```sh
apt install percona-mle percona-mle-python        # the JVM build with Python (pulls common, jdk, python-runtime)
apt install percona-mle-python-native             # the Python-only native build (pulls common, python-runtime)
```

On Red Hat Enterprise Linux and compatible systems (Oracle Linux, Rocky Linux, AlmaLinux):

```sh
dnf install percona-mle percona-mle-python         # also pulls percona-mle-selinux
dnf install percona-mle-python-native
```

The `percona-mle` package makes the JDK's AOT cache that JVM workers start from: once after installing, and again
whenever the JDK or Python packages are installed, upgraded or removed.

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

## Preparing pool workers: `mle.pool_prewarm`

Workers wait in a pool for new sessions. `mle.pool_prewarm` says what they prepare while they wait: `none`, or a
comma-separated list of:

- `python`: start Python in advance. Otherwise a worker starts Python on its first Python routine, which keeps idle
  workers small but makes that first call slower (about two seconds on the JVM build, a fraction of a second on the
  native build). It costs memory per waiting worker.
- `numpy`: start Python and import numpy too.
- `vectors` (the default): compile the vector helper's kernels (`com.percona.mle.api.Vectors`), so a session's first
  vector call runs at full speed (otherwise up to about half a second slower). It costs about 2 CPU-seconds per new
  pool worker, spent while the worker waits, and no memory to speak of. The native build has no vector helper and
  ignores it.

For example, `SET GLOBAL mle.pool_prewarm = 'numpy,vectors'`. Like `mle.memory_max`, it can be changed only while no
session holds a worker.

## Requirements

- Linux on x86-64, kernel 6.12 or later (Landlock ABI 6: the sandbox needs it).
- cgroup v2; for memory and process limits per worker, a cgroup delegated to mysqld (systemd: `Delegate=yes`).
  Without it MLE runs, with the workers' own heap limit and the system's limits.
- Percona Server with the language router (`component_lang_router`), of the server line the packages were built
  for.

## Debian and Ubuntu: AppArmor

Percona Server's package comes with an AppArmor profile for `mysqld` (`/etc/apparmor.d/usr.sbin.mysqld`), installed
in complain mode: it reports what it would refuse and refuses nothing. `percona-mle-apparmor` adds what MLE needs:

| File | What |
|---|---|
| `/etc/apparmor.d/percona-mle` | the profile `percona-mle` of `mle_launcher` and the workers: MLE's own files, the packages, the workers' cgroups; none of mysqld's data, keys or configuration, no network |
| `/etc/apparmor.d/abstractions/percona-mle-mysqld` | the rules `mysqld` needs: starting `mle_launcher` in that profile, talking to it, stopping workers |

The package adds one line to `/etc/apparmor.d/local/usr.sbin.mysqld`, the server profile's file of local additions,
and takes it out when it is removed:

```text
include <abstractions/percona-mle-mysqld>  # added by percona-mle-apparmor
```

MLE's profile is installed in complain mode too, as the server's is. To enforce both:

```sh
aa-enforce /etc/apparmor.d/usr.sbin.mysqld /etc/apparmor.d/percona-mle
systemctl restart mysql
```

When they are enforced:

- **Other directories.** The profiles know the packages' paths. A different `mle.dir`, `mle.java_home` or
  `mle.package_dir` (other than `/var/lib/mysql-mle-packages`) goes into `/etc/apparmor.d/local/percona-mle`, and
  the directories `mysqld` reads into `/etc/apparmor.d/local/usr.sbin.mysqld`:

    ```text
    /srv/mle-packages/ r,
    /srv/mle-packages/** mr,
    ```

- **The error log.** The launcher and the workers write to mysqld's error log, and AppArmor then checks mysqld's
  own writes to it against MLE's profile too. The profile allows `/var/log/mysql/`; for an error log somewhere else
  add `/path/to/error.log w,` to `/etc/apparmor.d/local/percona-mle` (without it MLE's own messages are lost).
- **A profile of `mysqld` that doesn't include `local/usr.sbin.mysqld`** needs the `include` line above itself;
  without it `INSTALL COMPONENT 'file://component_mle'` fails with `cannot start mle_launcher: ... Permission denied`.

Reload a changed profile with `apparmor_parser -r /etc/apparmor.d/percona-mle` (or `usr.sbin.mysqld`). The worker
sandbox (Landlock and seccomp) applies inside the profile as everywhere else. Workers keep the launcher's profile:
they start with `no_new_privs`, which rules out a profile of their own.

## Red Hat Enterprise Linux and compatible systems

**Landlock.** The sandbox needs the Landlock security module, which these kernels include but do not enable by
default. Enable it on the kernel command line and reboot:

```sh
grubby --update-kernel=ALL --args="lsm=landlock,lockdown,yama,integrity,selinux,bpf"
reboot
cat /sys/kernel/security/lsm        # must list landlock
```

Without Landlock, `INSTALL COMPONENT 'file://component_mle'` fails with a message saying so.

**SELinux.** MLE works with SELinux enforcing. The `percona-mle-selinux` package (installed automatically where
SELinux policy is installed) runs the MLE processes in SELinux domains of their own:

| Process | Domain | Allowed |
|---|---|---|
| `mysqld` | `mysqld_t` (unchanged) | starting `mle_launcher`; using the workers' sockets and shared memory; stopping workers |
| `mle_launcher` | `mle_launcher_t` | starting workers; creating their cgroups and shared memory |
| `mle_worker` | `mle_worker_t` | running code generated at run time (the JIT compiler); reading MLE's files and packages |

`mysqld` itself gets no new rights to run generated code or to change cgroups. The worker sandbox (Landlock and
seccomp) applies inside `mle_worker_t` as everywhere else.

Third-party packages: the policy labels `/var/lib/mysql-mle-packages` for `mle.package_dir`. For another
directory, label it the same way:

```sh
semanage fcontext -a -t mle_packages_t "/srv/mle-packages(/.*)?"
restorecon -R /srv/mle-packages
```

**cgroups.** For memory and process limits per worker, delegate a cgroup to mysqld in its systemd unit
(`systemctl edit mysqld`):

```ini
[Service]
Delegate=yes
DelegateSubgroup=mysqld
```
