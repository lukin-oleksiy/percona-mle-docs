<!-- check-page: skip: build commands for a development host -->
# Building

MLE is a separate product that builds against a configured and built Percona Server tree. The server needs only its
language router (`component_lang_router`).

## Prerequisites

- **A Percona Server tree, configured and built,** with the language router (`-DWITH_LANG_ROUTER=ON`, or `js_lang`
  enabled) and its unit-test libraries (GoogleTest).
- **GraalVM Community JDK 25** (`MLE_JAVA_HOME`, default `$JAVA_HOME`): it compiles the worker and runs it, and its
  `native-image` makes the native build.
- **Maven**, and network access to Maven Central and GraalPy's wheel index (the worker's dependencies, numpy).
- **Linux** with Landlock ABI 6 or newer (kernel 6.12+) and cgroup v2, to run the tests.

## Configure and build

```sh
cmake -G Ninja -S percona-mle -B BUILD-mle \
      -DMYSQL_SOURCE_DIR=<server source> -DMYSQL_BUILD_DIR=<server build> [-DMLE_BUILD_JDK=ON]
ninja -C BUILD-mle
```

- **The build type follows the server build's:** a debug server gives a debug MLE (assertions, `mle_debug_worker_pid()`).
- **For development** the build links `component_mle.so` and `mle/` into the server build's
  `plugin_output_directory`, so the server's MTR finds them (`-DMLE_LINK_INTO_SERVER_BUILD=OFF` to skip).

| Option | Default | What |
|---|---|---|
| `MYSQL_SOURCE_DIR`, `MYSQL_BUILD_DIR` | | the server tree to build against (required) |
| `MLE_JAVA_HOME` | `$JAVA_HOME` | the GraalVM JDK |
| `MLE_PYTHON_NATIVE` | `OFF` | also build the Python-only native worker (Native Image; minutes and about 12 GB of RAM) |
| `MLE_BUILD_JDK` | `OFF` | a trimmed GraalVM runtime (`jlink`) for the `percona-mle-jdk` package |
| `MLE_SELINUX` | `ON` with `selinux-policy-devel` | the SELinux policy module (RPM) |
| `MLE_LINK_INTO_SERVER_BUILD` | `ON` | link the built component into the server build |
| `MLE_DEBUG_BUILD_DIR` | | a release build packages the debug variants from this debug build (the server's debug layout) |
| `MLE_WITH_UNIT_TESTS` | `ON` | the GoogleTest binaries |
| `MLE_TEST_PYTHON_VENV` | the built one | a GraalPy venv with numpy for the tests |
| `MLE_VERSION`, `MLE_PACKAGE_RELEASE` | | package versions |

## What the build does

1. **C++:** `component_mle.so`, `libmle_host` (static), `mle_launcher`, `mle_worker` (the bootstrap), the unit tests,
   `mle_harness`.
2. **The worker jar:** Maven builds `mle-worker.jar` with its dependencies (GraalJS, GraalPy, the Truffle runtime;
   the GraalVM version is in `worker/pom.xml`).
3. **Language resources:** `Setup` extracts GraalPy's and GraalJS's resources into the build tree.
4. **The numpy venv:** the GraalPy Maven plugin builds a virtual environment with numpy (`worker/python/pom.xml`).
5. **The AOT cache:** a training run (`Train`) records the classes and profiles workers use at start, and the JDK
   builds an AOT cache from it; workers start about twice as fast with it. It belongs to one JDK and one set of
   jars and JVM options, so it is not installed: `mle_aot_cache` rebuilds it on the target (the packages run it).
   The JVM options it is built with must match `host/defaults.cc` (`-XX:+UseSerialGC -XX:-UseJVMCICompiler`).
6. **The native build** (`MLE_PYTHON_NATIVE=ON`): `worker/native/build-native.sh` runs `native-image --shared` on
   the jar without GraalJS, producing `libmleworker.so`; `mle_worker` loads it instead of the JVM.

The installed layout under `<plugin_dir>/mle/` (`mle.dir`): `mle_launcher`, `mle_worker`, `mle-worker.jar` and `deps/`
(its jars), `resources/` (the languages' resources, Python's standard library), `python-venv/` (numpy), `mle.aot`
(the AOT cache), `mle_aot_cache`, `mle_package`, `native/` (`libmleworker.so`, the native build), `jdk/` (the trimmed
JDK, if packaged), `packages/` (the DBA's packages, unless `mle.package_dir` is elsewhere).

## Packages

`cpack -G DEB` or `cpack -G RPM` in the build directory; the server must be built with `INSTALL_LAYOUT=DEB` or `RPM`.
One package per install component (`cmake --install BUILD-mle --component <c>`):

| Package | Contents |
|---|---|
| `percona-mle-common` | `component_mle`, `mle_launcher`, `mle_worker` |
| `percona-mle` | the JVM build: the worker jar, runtime jars, JavaScript and Java |
| `percona-mle-jdk` | the trimmed GraalVM runtime |
| `percona-mle-python-runtime` | Python's standard library and the numpy venv (both builds) |
| `percona-mle-python` | GraalPy for the JVM build |
| `percona-mle-python-native` | the native build |
| `percona-mle-tools` | `mle_package` |
| `percona-mle-selinux` | the SELinux policy module (RPM) |

Package scripts (`packaging/`) run `mle_aot_cache` when the JDK, jars or Python change (Debian triggers, RPM file
triggers).

## Docker images

```sh
cd percona-mle
docker/build-image.sh --tools <server release build> <MLE release build>
```

It installs the server and MLE components into an image (`percona-mle:dev`), with `docker/my.cnf`
(`mle.console = WARNINGS`, the package volume) and `mle-entrypoint.sh` (initialises the data directory, installs the
components, creates the `app` account); `--tools` also builds `percona-mle-tools:dev` with `mle_package`.
`docker/compose.yaml` runs both. The images don't include the native build.
