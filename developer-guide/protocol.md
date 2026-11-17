<!-- check-page: skip: a description of the code -->
# The wire protocol

mysqld and a worker talk through a shared-memory mailbox and a control socket. `include/mle/protocol.h` is the single
definition of the protocol: the host library (`codec.cc`, `worker.cc`), the launcher and the worker bootstrap include
it; the Java worker mirrors its constants in `Wire.java`, kept in sync by hand.

## Transport

- **The mailbox:** one memfd per worker, created and sealed by the launcher: a 4 KiB header (`RegionHeader`) and an
  arena (1 MiB by default) that carries one message at a time.
  - The header keeps each side's fields on its own cache line: the launcher's constants (magic `MLE1`, version,
    sizes), the host→worker sequence `to_w`, the worker→host sequence `to_h`, the two sides' sleep flags, the
    current message's header (`MessageHeader`), and the worker's statistics (`WorkerStats`: heap used and maximum, for
    `mle_memory_used`; young collections the memory watcher caused; whether the worker asks to be retired).
  - **The two sides strictly alternate.** The sender writes the message into the arena, fills the message header and
    increments its sequence word; the receiver spins for a while (`mle.spin_us`), then sleeps on the futex of that
    word. The sleep flags tell the sender whether a `FUTEX_WAKE` is needed, so a busy exchange makes no system calls.
- **Chunks:** a message larger than the arena goes in chunks: each has `MessageFlag::kMore` until the last, and the
  receiver answers each with `CONTINUE`. A logical message is at most 1 GiB; a worker's message at most 64 MiB (the
  host treats more as a violation; the worker refuses larger results and SQL itself, with an error).
- **The control socket** (`SOCK_SEQPACKET`): the host sends `INTERRUPT` (with the call's sequence number) and
  `SHUTDOWN`; its EOF tells the worker that mysqld is gone.

## Messages

Each message has a `MessageHeader`: `op`, `status` (for results), `flags`, `depth` (0 for a top-level call, +1 for
each call made while the worker waits for an SQL result), `len` (this chunk), `total_len`, `call_seq` (the top-level
call it belongs to).

| Op | Direction | Body |
|---|---|---|
| `READY` | w→h | the protocol version, worker class, languages, runtime version; with an error status, why the worker failed to start |
| `PREPARE` | h→w | routine id, language, name, entry (empty: a body), source, parameters, return type, `USING` libraries, schema, `check` |
| `CALL` | h→w | routine id, events (unused), arguments |
| `RESULT` | w→h | the return value, `OUT`/`INOUT` values, stdout, stderr, whether the console was cleared, the error |
| `SQL` | w→h | an `SqlAction` (execute, prepare, execute prepared, fetch, next result, close), statement id, text, parameters, flags, rows per chunk |
| `SQL_RESULT` | h→w | an error (errno, SQL state, message), OK (affected rows, last insert id, warnings, more results, parameter count, time), or rows (columns, the result set's total rows (0: unknown, a cursor), rows, more rows, more results, warnings, time) |
| `DROP` | h→w | forget a routine |
| `RESET` | h→w | forget all routines (the worker is kept for its account); globals and loaded libraries stay |
| `OPTIONS` | h→w | integer and decimal types for JavaScript, the session's time zone |
| `PARSE_LIBRARY` | h→w | a library's syntax check (`CREATE LIBRARY`) |
| `LIBRARY` | w→h | a library the routine imports: schema, name, the hash of the body the worker has |
| `LIBRARY_RESULT` | h→w | refused (with why), unchanged, or the body |
| `CONTINUE` | both | the next chunk, please |

`RESULT` statuses: `OK`, `GUEST_ERROR`, `INTERRUPTED`, `EXHAUSTED` (heap or stack), `INTERNAL` (a worker bug). The
host adds its own for what it observed: `DIED`, `KILLED` (after the interrupt grace), `PROTOCOL_ERROR`.

An **error** carries the language (`MLE-Type` for value conversions), the message, the stack frames (function, file,
line, column, end column, the source line), and an SQL errno and state when it is a MySQL error.

## Values

Little-endian; fixed-width integers; a string is a `u32` length and UTF-8 bytes (the host converts character sets). A
value is a tag and its payload:

| Tag | Payload | SQL types |
|---|---|---|
| `NULL` | | |
| `I64`, `U64` | 8 bytes | integers, `YEAR`, `BIT` |
| `F64`, `F32` | IEEE 754 | `DOUBLE`, `FLOAT` |
| `DECIMAL` | text, `[-]digits[.digits]` | `DECIMAL` |
| `STRING` | text | character types, `ENUM`, `SET` |
| `BYTES` | `u32` length, bytes | binary types, `BLOB` |
| `DATE`, `TIME`, `DATETIME`, `TIMESTAMP` | year, month, day, hour, minute, second, microsecond, sign | temporal types |
| `JSON` | text | `JSON` |
| `VECTOR` | `u32` dimensions, padding to 16 bytes from the message start, `float32` values | `VECTOR` |

A parameter's type (`ParamType`) adds the declared type: unsigned, length, precision, scale, and the SQL type name;
the worker converts results to it (ranges, rounding, `SET` and `ENUM` text). `VECTOR` data is aligned so that the
worker can copy it into a numpy array or a `float[]` with one memory copy.

## Limits

| Limit | Value | Why |
|---|---|---|
| a worker's message | 64 MiB | bounds what a worker can make mysqld allocate |
| values in one list | 65 535 | MySQL's limit of statement placeholders |
| `VECTOR` dimensions | 16 383 | MySQL's |
| nesting depth | 32 | routine → SQL → routine ... |
| stack frames in an error | 1000 | |

## Changing the protocol

- Change `protocol.h` first (the comments are the specification), then `codec.h`/`codec.cc` and their tests
  (`mle_codec-t`; add a seed to `mle_codec_fuzz-t`), then `Wire.java`, `MessageReader`/`MessageWriter` users in the
  worker.
- A new field goes at the end of a message body; the worker reads it with the same order. Host and worker are always
  built and installed together, so the version (`kVersion`) changes only for an incompatible layout.
- The host must validate everything it decodes (`codec.h` treats input as hostile): counts, sizes, tags, UTF-8.
