# packet-ask

[Korean](README.ko.md)

A local CLI that sends only a **scrubbed packet** to a **SUB** agent. The MAIN agent is whichever session you are in now.

It copies the files or diff you choose, scrubs them, then either runs an official CLI against that packet or prints `packet.md` to paste elsewhere.

> This tool shrinks what you send on purpose. It does not guarantee no leakage and does not stop vendor training. Vendor terms still apply. See [SECURITY.md](SECURITY.md) ([Korean](SECURITY.ko.md)).

MIT licensed. See [LICENSE](LICENSE).

- PyPI: [pypi.org/project/packet-ask](https://pypi.org/project/packet-ask/)
- Repository: [github.com/ictechgy/packet-ask](https://github.com/ictechgy/packet-ask)

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 0.10.9+ (same lower bound as `uv_build` in `pyproject.toml`)
- GLM / Claude SUB runs: a `claude` CLI on the allowlist path (origin signatures are not verified)
- Kimi runs: a `kimi` CLI on the allowlist path
- `paste` / `grok` / `agy` print a packet and do not launch a vendor. Run
  `packet-ask doctor` for the measured reason each one stays paste-only

Credentials come from a dedicated environment variable, a packet-ask-owned macOS Keychain item, or an explicitly requested one-run prompt. **This tool does not read `.env`, ZCode, Claude Code, arbitrary key files, or key commands.** Never put key values on the command line; they land in shell history. The same applies to the question: `--question` is argv and is visible in the process table and in shell history, so prefer `--question-stdin`. Stdin keeps the question out of argv, but it does not control what an interactive shell records; read a sensitive question from a file rather than a typed heredoc. Variable names are in [.env.example](.env.example), and the complete source contract is in [docs/key-sources.md](docs/key-sources.md). The executable allowlist is in [SECURITY.md](SECURITY.md). Claude-family launches use official bare mode, an empty built-in tool set, and strict explicit empty MCP configuration. `doctor` only checks that bounded help text mentions those required flags; it does not prove an OS sandbox.

## Install

```bash
uv tool install packet-ask
packet-ask install-skills
packet-ask doctor
```

If it is already installed, run `uv tool upgrade packet-ask`. `pipx install packet-ask` and `pip install packet-ask` in a venv also work.

From the GitHub default branch:

```bash
uv tool install git+https://github.com/ictechgy/packet-ask
```

Local checkout:

```bash
uv sync
uv run packet-ask doctor
```

`install-skills` writes `SKILL.md` to `~/.claude/skills/packet-ask`, `~/.grok/skills/packet-ask`, `~/.codex/skills/packet-ask`, and `~/.agents/skills/packet-ask`. After that, `/packet-ask` or a phrase like "review with kimi" should make MAIN call this CLI.

## Scope

`review` and `research` are the only task commands. `review` requires **one** of the flags below. It does not send the whole working tree by default.

| Flag | What is sent |
| --- | --- |
| `--files` | the listed files |
| `--diff` | the given git range |
| `--staged` | staged diff |
| `--unstaged` | uncommitted working-tree diff |

`research` does not attach local files or diffs by default. The only exception is `--include-files`. `--diff` and `--staged` are rejected.

`inspect review` and `inspect research` build and verify the same scrubbed packet
but print metadata only after cleanup. They do not load a provider, inspect a
credential, calculate a provider timeout, or launch a vendor. Human output is
one escaped line; `--json` returns only the fixed packet summary fields.
`--breakdown` additively reports scrubbed question bytes, framing bytes, and
per-file/diff scrubbed bytes, logical lines, and allowlisted redaction counts.
Line counts describe only the post-scrub payload, use LF as the separator, and
are per-item only (`0` for a zero-byte file). A diff count covers its complete
rendered diff, including headers and hunks—not just changed lines. This
per-item shape metadata is disclosed by design; the question and item body are
never returned.

`--line-numbers` opt-in adds a fixed-width gutter to scrubbed full-file content
inside `packet.md`, so a review can cite packet-local lines. It does not alter
the scrubbed files under `files/`, never decorates unified diffs, and counts all
gutter/note bytes against `--max-bytes`. The numbers are stable only for the
receipt's packet digest. With the flag off, packet bytes remain unchanged.

`--selected-tree` opt-in adds a deterministic tree made only from paths already
selected by `--files` or `--include-files`. It never walks the repository,
shows repeated selections once, renders ASCII-escaped labels in an inert fenced
text block, and counts the tree against `--max-bytes`. Diff-only and
question-only packets reject the flag instead of silently widening or ignoring
its scope.

File section headings preserve ordinary Unicode paths but escape line/control,
bidi, backtick, and HTML-delimiter characters. The private artifact under
`files/` keeps the exact selected filename.

`--max-files` applies to explicit files and diff paths. `--max-bytes` applies to
the final UTF-8 `packet.md`, including framing and path labels. Reads stop at
the configured bound, and explicit binary or non-UTF-8 files are rejected.

`--preflight-timeout` defaults to 30 seconds. One monotonic absolute deadline
is shared by real-fd question stdin, worktree discovery, diff name-status, diff
content, and packet-local `git init`; each Git call also keeps its existing
30-second cap. A supplied positive value is used exactly. Regular file reads and
CPU redaction are bounded by size but are not forcibly preempted by this timer.

After normal redaction, verification also scans a detection-only Unicode
shadow. NFKC-compatible characters, format controls, equivalent dots and
dashes, and Unicode decimal digits can expose obfuscated international email or
phone candidates. Known token families, secret literals, URL userinfo, and PEM
headers are checked against the same shadow; canonical dotted Korean mobile
numbers are scrubbed and mixed-separator forms fail closed. The packet text is
never normalized or rewritten from this shadow; a candidate fails closed instead.
Ambiguous Unicode matrix expressions
with an unrecognized ASCII attribute-like suffix remain allowed to reduce code
false positives, so this is still a denylist rather than a no-leak guarantee.

If `--timeout` is omitted, launch providers use the final packet size: up to
64 KiB gets 1200 seconds, up to 128 KiB gets 1500 seconds, and larger packets
get 1800 seconds. A supplied `--timeout` is used exactly without clamping. The
larger defaults do not delay successful calls; they only postpone failure for a
real hang, which can still be interrupted with Ctrl+C. CI and unattended runs
should set an explicit timeout. Paste and dry-run receipts show the resolved
value as informational, but no provider deadline is applied.

## Stated limits

Every success surface states its own limits. `receipt` and `inspect` summaries
carry a fixed `guarantees` object — `leakage: not-guaranteed`,
`vendor_training: not-restricted`, `vendor_local_copy: uncontrolled`,
`cwd_sandbox: none`, `redaction: denylist`, `doctor: help-text-only`,
`policy_gate: lexical-tripwire` — and the human receipt line ends with
`guarantees=leakage:not-guaranteed,cwd_sandbox:none,redaction:denylist`.

These are code constants, not computed results. The disclaimer keys therefore
cannot drift into a promise. The three keys that assert a mechanism exists
(`redaction`, `doctor`, `policy_gate`) are additionally pinned to real behavior
by tests, because a constant that outlives its mechanism would be a
machine-readable falsehood.

**This list is not exhaustive.** It names the limits that are most often
misread, not every risk. The failure envelope is unchanged and still carries
only a fixed code, kind, and message.

`doctor` states its own verification level the same way. After the provider
rows it prints one fixed line:

```
packet-ask doctor signals=verification:flags-mentioned,sandbox:none,signatures:not-checked
```

`doctor` runs before any receipt exists, so this is where the offset has to
arrive. `verification: flags-mentioned` is pinned to real behavior; the other
two say what `doctor` never attempted. It builds no sandbox and checks no
signature or hash.

Both lines are append-only token sequences. Parse them by whitespace and
`key=value`; do not anchor a regex to the end of the line. They are machine
surfaces and do not change with `PACKET_ASK_LANG`.

## Disclosure surface

The design says the user picks the packet. When a skill drives this CLI, the
agent picks `--files`. The scope flags only stop "the whole tree by accident";
they do not stop "256 KiB the agent chose".

Commit a `.packet-ask-surface` file at the worktree root listing the path
prefixes this repository may disclose:

```
# what this repository may send to a SUB
src
docs/public
```

With that file present, an explicit `--files` / `--include-files` path outside
the declared prefixes is rejected with exit 11 before any vendor starts. Without
the file, nothing changes. Matching is by path component, so `src` does not open
`srcret/`. Absolute paths, `..`, globs, control characters, a symlinked
declaration, and an empty declaration are all rejected.

`--outside-surface` overrides the check for one run, and the receipt, `inspect`
summary, and ledger all record `surface: overridden` instead of `enforced`.

Diff paths are checked too. `--diff <ref>` can reach history without touching
the worktree at all, so exempting diffs would leave a channel that changes
nothing a reviewer would see.

### What it does not do

This is not a leak-prevention allowlist and it is not a sandbox. Its only
mechanism is that widening the scope requires editing a committed file, so the
edit shows up in `git status` or as a new commit and lands in the review you
already do.

- A declaration is about **paths, not contents**. Declaring `src` does not mean
  `src` holds no secrets; the redaction denylist still applies and still is a
  denylist.
- A hard link inside a declared prefix that points at a file outside it is
  accepted, because a hard link *is* the file and there is no original to
  prefer. Creating one is itself a visible worktree change.
- Content injected into an ordinary diff is not caught. The edit is visible, but
  far less salient than a change to the declaration file.
- A malformed declaration fails closed and `--outside-surface` does not bypass
  it. Fix the file.

## Egress ledger

The receipt goes to stderr once and is gone with the scrollback. When a skill
drives this CLI, the agent picks `--files`, so there is no surface left for a
human to ask what actually went out.

Set `PACKET_ASK_LEDGER` to an absolute path and every task run appends one JSON
line before the vendor starts. The line records a run that reached the point of
egress, not a confirmed delivery — a vendor that fails afterwards still leaves
an entry, which is the safe direction for an audit surface. Each line holds: timestamp, mode, provider, selector, relative
paths, bytes, packet digest, redaction counts, and the resolved timeout. The
question and the file bodies are never written.

The path must be absolute, must not be inside the git worktree, must not be a
symlink, and must be owned by the current user. The worktree check compares
device and inode, not path strings, so a case-insensitive filesystem does not
bypass it. The file is opened `O_APPEND`, `O_NOFOLLOW`, and `O_NONBLOCK`, and
its mode is forced to `0600` before the first write even if the file already
existed. **If the entry cannot be written, the vendor does
not run** and the command exits 13. A ledger that silently skips entries is
worse than none. Leave the variable unset to keep the feature off.

## Usage

```bash
packet-ask providers
packet-ask credentials status

# Store through the interactive macOS Keychain prompt; no key in argv/history
packet-ask credentials set glm --store macos-keychain --access command

# Question on stdin; it stays out of argv and the process table
packet-ask review --provider paste --files src/app.py --question-stdin <<'EOF'
Find race conditions in this code
EOF

# Packet only; do not launch a vendor
packet-ask review --provider paste --files src/app.py --question "Find race conditions in this code"
packet-ask review --provider paste --files src/app.py --json --question "Find race conditions in this code"

# Metadata only; no packet body, provider, or credential access
packet-ask inspect review --files src/app.py --question "Find race conditions"
packet-ask inspect review --diff HEAD --json --question "Review this change"
packet-ask inspect review --diff HEAD --breakdown --json --question "Size this packet"

# Increase only the local stdin/Git preflight budget; provider timeout is separate
packet-ask inspect review --diff HEAD --preflight-timeout 60 --question "Review this change"

# GLM. auto = dedicated env first, then packet-ask-glm in macOS Keychain
packet-ask review --provider glm --credential-source auto --diff HEAD --question "Review this change"

# Optional non-sensitive heartbeat every 30 seconds during provider launch
packet-ask review --provider glm --diff HEAD --progress --question "Review this change"

# Uncommitted working-tree diff. review without a scope flag is rejected
packet-ask review --provider paste --unstaged --question "Review this change"

# Anthropic Claude SUB. Key: PACKET_ASK_CLAUDE_KEY. Parent BASE_URL is unchanged
packet-ask review --provider claude --files src/app.py --question "Review this code"

# Kimi. Key: PACKET_ASK_KIMI_KEY
packet-ask review --provider kimi --files src/app.py --question "Review this code"

# grok/agy stay paste. grok resolves its real binary through the vendor home
# and takes no dedicated key; agy needs the task in argv
packet-ask review --provider grok --files src/app.py --question "Review this code"

# Research. Local files are off by default
packet-ask research --provider paste --question "What usually breaks in a Tailwind v4 migration?"

packet-ask doctor
```

Kimi is official `kimi --quiet` one-shot. It does not open an interactive session. It refuses to run without a resolved dedicated Kimi credential. Tools are disabled with a `tools: []` agent file and a non-matching `[tools] enabled` list. `KIMI_CODE_HOME` is only the isolated profile `~/.config/packet-ask/providers/kimi/kimi-code`. Do not run `kimi` in the real repo.

Kimi session cleanup must succeed before successful output is returned. If a
provider, output-guard, or signal failure already exists, a simultaneous session
cleanup failure is reported as a fixed non-sensitive warning and never replaces
the original exception or exit status.

Kimi runs sharing the isolated `KIMI_CODE_HOME` are serialized by a private,
non-inheritable advisory lock held through config creation, provider execution,
and session cleanup. A second run that cannot acquire it within 30 seconds fails
before touching the shared profile or launching a vendor.

GLM uses the official `claude` binary. **It does not change the parent shell `ANTHROPIC_BASE_URL`.** Only the child environment gets the [Z.ai Claude Code endpoint](https://docs.z.ai/scenario-example/develop-tools/claude) and the resolved dedicated GLM credential. GLM and Claude use Claude Code's documented `--bare` and `--tools ""`, plus an inline empty `--mcp-config` with `--strict-mcp-config`; the child also disables claude.ai MCP servers. Child environments set `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, and `DISABLE_ERROR_REPORTING`. That reduces tool/config and local-session exposure. It does not prove an OS sandbox or that the vendor stores nothing.

Task commands default to `--credential-source auto`: the dedicated environment
variable wins, then the canonical macOS Keychain item is tried. `env`,
`keychain`, and `prompt` select exactly one source and never silently fall back.
`prompt` requires an interactive terminal and never persists the value. Status
checks Keychain item existence without retrieving its password. Resolved keys
from every source are included in raw and terminal-normalized output guards.
Task-time reads use fixed non-sensitive messages to distinguish a missing item,
an inaccessible item, a timeout, an invalid value, and an indeterminate local
read failure; JSON errors and code-level classification remain generic.
These sources are selected through an immutable builtin backend registry;
`auto` contains only `env` then `keychain`. There is no user backend
registration, arbitrary command, key-file, or third-party settings adapter.

On success, stderr prints a receipt before launch (`packet-ask receipt …`) and millisecond phase times after (`packet-ask timing …`). `--json` adds a `timing` object. Neither line contains keys. Receipt paths are JSON escaped, and terminal control sequences are removed from untrusted provider output before it is printed.
Explicit `--progress` adds only `packet-ask progress phase=launch elapsed_ms=…`
to stderr every 30 seconds while the provider call is active. It is off by
default, contains no provider/path/key/body data, and stops before final timing
or success output.

With `--json`, argparse and runtime failures also return one `packet-ask.v1`
stdout object with `ok: false` and stable `error.code`, `error.kind`, and a
generic English `error.message`. Raw argv, exception text, paths, keys, and
tracebacks are excluded. The process exit code is unchanged. Without `--json`,
the existing human stderr behavior remains unchanged.

Receipt JSON adds `timeout_seconds`, `timeout_source` (`auto` or `explicit`),
and `timeout_applies`. The `packet-ask.v1` schema is additive; consumers should
ignore unknown receipt keys. The four-key millisecond `timing` object is unchanged.

Packet temp dirs live in the OS cache, not the git worktree. cwd is not a sandbox. A `PACKET_ASK_CACHE_DIR` inside the worktree is rejected. Every new packet holds a private lease; a later run removes packet data only when that lease is unlocked and at least 24 hours old. Active, fresh, symlinked, non-private, and legacy directories without a lease are not reaped. `.gitignore` entries for `.packet-ask-tmp/` and `packet.md` only stop leftover files from being committed.

Every Git subprocess uses the same bounded process-group runner. Worktree
discovery, diff collection, and packet-local `git init` share a deadline and
byte limits. Ctrl+C, SIGTERM, and SIGHUP terminate registered Git/provider
groups and run packet cleanup before propagating. Successful
stdout is emitted only after packet cleanup succeeds. Packet payload bytes and
digest, plus unchanged user provider overlays, are reused in-process to avoid
repeated reads, hashes, and TOML parses.

User config `~/.config/packet-ask/providers.toml` adds **paste aliases only**. It does not accept executables, argv, env, adapter IDs, launchers, probes, or registration hooks. Builtin launch dispatch and doctor probe kinds come from an immutable code registry.
Alias labels and notes are bounded and reject terminal, bidi, and line/paragraph
control characters before either human or JSON output. ZWNJ and ZWJ remain
allowed for normal language and emoji sequences.

The implementation/incident question gate is a conservative lexical check, not
a proof of intent. Launch adapters disable vendor tools and use the packet as
the child cwd, but this is not OS-level filesystem confinement.

```toml
version = 1
[providers.gemini]
label = "Gemini CLI"
```

## Skills

`packet-ask install-skills` installs into harness homes. The skill only tells MAIN to call `packet-ask`. Packet selection and vendor-tool disabling are enforced by the CLI; they are not an OS sandbox.

## Exit codes

`10`–`14` mean the vendor process was never started. Task termination preserves
the conventional signal exit status: SIGHUP is 129, SIGINT is 130, and SIGTERM is 143.

| Code | Meaning |
|---:|---|
| 0 | success |
| 1 | internal error |
| 2 | usage error |
| 10 | policy reject (implementation, incidents, ...) |
| 11 | scope reject |
| 12 | redaction / re-check failed |
| 13 | could not confirm launch conditions |
| 14 | over budget |
| 20 | provider or key missing |
| 21 | provider failed |
| 22 | output guard failed (dedicated key leak or oversized output) |

## Development

```bash
uv sync --group dev
uv run pytest
```

The current confinement hardening rationale is in [docs/hardening.md](docs/hardening.md).
Runtime/process and hot-path decisions are in [docs/runtime-hardening.md](docs/runtime-hardening.md).

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) Copyright (c) 2026 Coden
