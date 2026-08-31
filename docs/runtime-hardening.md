# Runtime hardening design

This batch closes bounded-process and cleanup gaps without broadening provider,
scope, or task authority.

## Process lifecycle

- Worktree discovery and packet-local `git init` receive explicit deadlines and
  stable packet-ask errors. Small metadata output is capped before parsing.
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

## Deferred

Stale-packet garbage collection, SIGTERM handlers, JSON error envelopes, and a
provider-adapter registry need separate concurrency or compatibility design.
They are not bundled into this change.
