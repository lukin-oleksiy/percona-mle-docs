<!-- check-page: skip: a description of the code -->
# Processes and the sandbox

## The engine

`component_mle`'s engine (`engine.cc`) owns the launcher and the workers:

- **Start:** `INSTALL COMPONENT` starts it (`engine_start`): it picks the build (`mle.worker`), finds the cgroup base
  (`AUTO`: the parent of mysqld's own cgroup, if mysqld may manage it), describes the worker class (executable, JVM
  options, prewarm list, limits) and starts the launcher. If the engine can't start, `INSTALL` fails.
- **Restart:** variables that shape workers (`mle.memory_max`, `heap_max`, `malloc_mmap_threshold`, `pool_size`,
  `pool_prewarm`, `vector_threads`) restart the engine. That is refused while sessions hold workers. A restart bumps the generation:
  workers of the old generation are not cached.
- **A dead launcher** is restarted on the next request, at most once a second.
- **`mle_packages_refresh()`** restarts the engine too, so new workers see the package directory.

## The launcher

mysqld starts `mle_launcher` with `posix_spawn` and talks to it over a `SOCK_SEQPACKET` socket (the configuration is
the first datagram). The launcher is single-threaded: a poll loop over that socket and every worker's pidfd.

- **Spawning a worker** (`spawn`):
  1. a sealed memfd for the mailbox, with its header written;
  2. a `SOCK_SEQPACKET` control socket pair and an error pipe;
  3. a cgroup leaf (`memory.max`, `memory.swap.max = 0`, `memory.oom.group = 1`, `pids.max`);
  4. `clone3(CLONE_PIDFD | CLONE_INTO_CGROUP)`, or `clone(CLONE_PIDFD)` where container runtimes refuse `clone3`
     (the child then joins the cgroup itself);
  5. in the child: parent-death signal, signals reset, descriptors 0–2 to `/dev/null`, 3 = mailbox, 4 = control, a
     file-descriptor limit, no core dumps, `oom_score_adj = 1000`, Landlock, then `execve` of `mle_worker`;
  6. the launcher waits on the error pipe until `exec` succeeded.
- **The pool:** the launcher keeps `mle.pool_size` workers started per class, refilling one per loop turn; a class
  whose pool workers keep dying (three times) stops being refilled.
- **Handing out** (`acquire`): the mailbox, the control socket and the pidfd go to mysqld with `SCM_RIGHTS`; the
  launcher closes its copies and only reaps the worker (exit status, OOM kills) later.
- **mysqld gone:** on EOF the launcher kills every worker.

## The worker's lockdown

| Layer | Where | What |
|---|---|---|
| Landlock (ABI 6+) | `launcher/child.cc` | read and execute: system libraries and MLE's files; read: a short list; nothing writable; mysqld's plugin and data directories hidden; TCP bind/connect handled (none allowed); abstract Unix sockets and signals scoped to the worker |
| seccomp | `worker/bootstrap/seccomp.cc` | a deny list: other architectures; new processes (`fork`, `vfork`, `exec`; `clone` only for threads, `clone3` refused); sockets except `AF_UNIX` socket pairs; `ptrace` and other processes' memory; signals to other processes; namespaces, mounts, keys, BPF, io_uring, userfaultfd, perf events, modules; `prctl` can't undo the dumpable and parent-death settings |
| cgroup | `launcher/cgroup.cc` | memory (no swap; the whole worker is killed on OOM), number of tasks |
| `PR_SET_DUMPABLE 0` | bootstrap | no core dumps or `/proc` access to the worker's memory by others |
| SELinux (RHEL) | `packaging/selinux/percona_mle.te` | `mysqld_t` → `mle_launcher_t` → `mle_worker_t` (`execmem` for the JIT compilers only there) |
| polyglot access | `Polyglot.builder` | JavaScript and Python: host access to Java objects but no class loading, reflection, threads, processes, JMX or the worker's classes; libraries from a read-only virtual file system |

The bootstrap installs seccomp and then starts the JVM in the same process through JNI (`dlopen` of `libjvm`, or of
the native image's `libmleworker.so`), on a thread with a known stack. Nothing is executed after the lockdown.

## Workers and sessions

- **A session's workers:** one `Slot` per principal (`'user'@'host'`), holding the worker and the routines prepared
  in it. The principal is computed for every call (the routine's definer or the invoker).
- **Getting a worker** (`Session::worker_for`): reuse the session's, else the account's newest cached worker
  (`engine_reuse_worker`), else a new one from the pool (`engine_new_worker`). `mle.max_workers` bounds workers held
  by sessions (error 6112 beyond it).
- **Releasing:** when the session ends (or `COM_CHANGE_USER`), its workers are released to the cache: the engine
  sends `RESET` (routines dropped, the heap trimmed when idle) and keeps the worker for the same account, up to
  `mle.worker_cache_size`, for `mle.worker_cache_timeout` seconds. A worker is not cached if it is tainted (a thread
  left behind, out of memory), of an old generation, or its account was dropped (`DROP USER`, `RENAME USER`).
  `mle_session_reset()` releases without caching.
- **A dead worker** is dropped; a nested call's worker is kept until the outermost request ends.

## The threat model

- **Routine code is untrusted.** It may be written by any account with a language privilege, and it may load native
  code (numpy, PyPI packages). It must not read or change other accounts' data, mysqld's memory or files, or the
  host, and its failures must stay in its worker.
- **The worker is untrusted by mysqld.** The host copies every message out of shared memory before validating and
  decoding it; sizes, counts and values are checked; a violation ends the worker. A worker can make mysqld allocate
  at most `kMaxWorkerMessage` (64 MB) per message.
- **What a routine can reach:** SQL as its account (through mysqld, with the account's privileges), the data passed
  to it, and the state of its own worker (which belongs to one account). Not reachable: files, the network, other
  processes, other accounts' workers.
- **Not covered:** CPU time (a routine can use its share until `KILL` or `MAX_EXECUTION_TIME`); side channels
  between workers on the same host.
