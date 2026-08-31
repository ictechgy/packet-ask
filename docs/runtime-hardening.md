# Runtime hardening design

This batch closes bounded-process and cleanup gaps without broadening provider,
scope, or task authority.

## Process lifecycle

- Worktree discovery and packet-local `git init` receive explicit deadlines and
  stable packet-ask errors. Small metadata output is capped before parsing.
- Task and inspect commands create one configurable preflight deadline. Real-fd
  question stdin and rev-parse/name-status/diff/packet-init Git calls reuse the
  same object, while each Git process retains its individual 30-second cap.
- A `KeyboardInterrupt` or other `BaseException` after process creation always
  terminates the vendor or Git process group before propagating.
- Spawn failures remain normal packet-ask failures and never expose a traceback.

## Result and cleanup ordering

- Provider output is prepared in memory, but success timing/stdout is emitted
  only after the packet has been removed.
- A cleanup failure on a successful task becomes a stable internal failure with
  no success body. When a provider or policy failure already exists, cleanup
  failure is reported as a non-sensitive warning and never replaces the
  original exit code.
- Kimi session cleanup follows the same precedence: success still requires
  cleanup, while an existing provider/output/signal exception is preserved and
  a simultaneous cleanup failure becomes a fixed warning.
- A private run lock serializes the shared Kimi config/session lifecycle. It is
  acquired before profile mutation, held through cleanup, bounded to 30 seconds,
  and never inherited by the provider child. Body errors are not reclassified as
  lock failures.
- Removing the shared cache parent tolerates `ENOENT` and `ENOTEMPTY`, which are
  expected under concurrent packet-ask processes.

## Confinement invariants

- `_collect_scope` rejects wrong-mode file flags itself even if a future caller
  changes policy-check ordering.
- Public redaction metadata is an exact allowlist of non-negative integer
  counters. Internal `extras` or later fields never enter packet manifests,
  receipts, JSON, or stderr.
- Private-key verification recognizes concrete PEM headers instead of matching
  the detector's own regular-expression source.

## Hot-path ownership

- `Packet` owns its rendered text, UTF-8 bytes, and digest. Receipt and launch
  consumers reuse them rather than rereading and rehashing `packet.md`.
- User paste aliases are cached by path, mtime, size, and selected language.
  Successful parses are cached; changed files invalidate the entry.
- Failed provider `--help` probes are not cached for the process lifetime.
- Provider `--help` stdout and stderr share one byte cap and deadline. Timeout,
  output excess, and interrupts terminate the probe process group.

## Builtin adapter registry

- An immutable code mapping binds each builtin ID to its current CLI launcher
  name and doctor probe kind. Dispatch resolves the current module callable at
  invocation time so existing test and embedding seams remain intact.
- Catalog mode and adapter identity are revalidated at the shared dispatch and
  doctor boundary. Inconsistent state fails with the confinement exit code.
- User aliases have no adapter ID and cannot configure executables, argv, env,
  launchers, doctor kinds, or registration hooks; they remain paste-only.

## Stale packet leases

- Every new packet owns an open, non-inheritable directory advisory lock and a
  0600 lease marker.
- Startup cleanup skips active, fresh, symlinked, non-private, other-owner, and
  legacy directories without a lease. The fixed stale threshold is 24 hours.
- Cleanup is anchored to open directory descriptors. It removes packet content
  before the lease marker, so an interrupted cleanup remains eligible for a
  later retry and a path replacement cannot redirect recursive deletion.

## Task signals

- Task-only SIGTERM and SIGHUP handlers raise conventional 143 and 129 exits.
- Spawn and packet return/assignment temporarily defer delivery without leaving
  the signals blocked in an exec child. The pending signal is replayed only
  after the process group or packet has been registered for cleanup.
- Packet removal blocks those signals only for the deletion critical section.
  Previous handlers and the calling thread's original mask are restored on all
  exits. Non-task commands do not install handlers.

## JSON failures

- `--json` parse and runtime failures emit one stdout `packet-ask.v1` object and
  preserve the numeric process exit code.
- Error fields come from a fixed code mapping. Raw argv, exception text, paths,
  credentials, provider stderr, and tracebacks are never serialized.
- Human parse usage and runtime stderr remain unchanged when `--json` is absent;
  help remains human-readable even when `--json` is also present.

## Packet inspection

- `inspect review|research` builds the normal verified packet and withholds its
  summary until descriptor-relative packet cleanup succeeds.
- It never loads a provider, credential, or provider timeout and never returns
  the question, packet body, temporary root, or provider-derived data.
- The public summary is limited to mode, selector, escaped relative paths, file
  count, final bytes, allowlisted redaction counts, and packet SHA-256.

## Shared packet pipeline

- Task and inspect commands resolve question/policy into the same `PacketInputs`
  shape before provider lookup or filesystem access.
- One context owns worktree resolution, scope collection, packet budget, stale
  GC, packet construction, signal-safe cleanup, and success-output gating.
- Provider lookup remains after policy but before explicit review-scope failure,
  preserving existing error precedence. Paste/research/brainstorm remain
  question-only; only review requires an explicit scope.
