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
- `paste` / `grok` / `agy` print a packet and do not launch a vendor

Credentials come from a dedicated environment variable, a packet-ask-owned macOS Keychain item, or an explicitly requested one-run prompt. **This tool does not read `.env`, ZCode, Claude Code, arbitrary key files, or key commands.** Never put key values on the command line; they land in shell history. Variable names are in [.env.example](.env.example), and the complete source contract is in [docs/key-sources.md](docs/key-sources.md). The executable allowlist is in [SECURITY.md](SECURITY.md). `doctor` only checks that bounded help text mentions required flags. It does not prove a no-tools sandbox.

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

`review` requires **one** of the flags below. It does not send the whole working tree by default.

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

`--max-files` applies to explicit files and diff paths. `--max-bytes` applies to
the final UTF-8 `packet.md`, including framing and path labels. Reads stop at
the configured bound, and explicit binary or non-UTF-8 files are rejected.

After normal redaction, verification also scans a detection-only Unicode
shadow. NFKC-compatible characters, format controls, equivalent dots and
dashes, and Unicode decimal digits can expose obfuscated international email or
phone candidates. The packet text is never normalized or rewritten from this
shadow; a candidate fails closed instead. Ambiguous Unicode matrix expressions
with an unrecognized ASCII attribute-like suffix remain allowed to reduce code
false positives, so this is still a denylist rather than a no-leak guarantee.

If `--timeout` is omitted, launch providers use the final packet size: up to
64 KiB gets 1200 seconds, up to 128 KiB gets 1500 seconds, and larger packets
get 1800 seconds. A supplied `--timeout` is used exactly without clamping. The
larger defaults do not delay successful calls; they only postpone failure for a
real hang, which can still be interrupted with Ctrl+C. CI and unattended runs
should set an explicit timeout. Paste and dry-run receipts show the resolved
value as informational, but no provider deadline is applied.

## Usage

```bash
packet-ask providers
packet-ask credentials status

# Store through the interactive macOS Keychain prompt; no key in argv/history
packet-ask credentials set glm --store macos-keychain --access command

# Packet only; do not launch a vendor
packet-ask review --provider paste --files src/app.py --question "Find race conditions in this code"
packet-ask review --provider paste --files src/app.py --json --question "Find race conditions in this code"

# Metadata only; no packet body, provider, or credential access
packet-ask inspect review --files src/app.py --question "Find race conditions"
packet-ask inspect review --diff HEAD --json --question "Review this change"

# GLM. auto = dedicated env first, then packet-ask-glm in macOS Keychain
packet-ask review --provider glm --credential-source auto --diff HEAD --question "Review this change"

# Uncommitted working-tree diff. review without a scope flag is rejected
packet-ask review --provider paste --unstaged --question "Review this change"

# Anthropic Claude SUB. Key: PACKET_ASK_CLAUDE_KEY. Parent BASE_URL is unchanged
packet-ask review --provider claude --files src/app.py --question "Review this code"

# Kimi. Key: PACKET_ASK_KIMI_KEY
packet-ask review --provider kimi --files src/app.py --question "Review this code"

# grok/agy still paste; they do not run a no-tools one-shot yet
packet-ask review --provider grok --files src/app.py --question "Review this code"

# Research. Local files are off by default
packet-ask research --provider paste --question "What usually breaks in a Tailwind v4 migration?"

packet-ask doctor
```

Kimi is official `kimi --quiet` one-shot. It does not open an interactive session. It refuses to run without a resolved dedicated Kimi credential. Tools are disabled with a `tools: []` agent file and a non-matching `[tools] enabled` list. `KIMI_CODE_HOME` is only the isolated profile `~/.config/packet-ask/providers/kimi/kimi-code`. Do not run `kimi` in the real repo.

GLM uses the official `claude` binary. **It does not change the parent shell `ANTHROPIC_BASE_URL`.** Only the child environment gets the [Z.ai Claude Code endpoint](https://docs.z.ai/scenario-example/develop-tools/claude) and the resolved dedicated GLM credential. GLM and Claude child environments also set `CLAUDE_CODE_DISABLE_AUTO_MEMORY`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, and `DISABLE_ERROR_REPORTING`. That reduces local session residue. It does not prove the vendor stores nothing.

Task commands default to `--credential-source auto`: the dedicated environment
variable wins, then the canonical macOS Keychain item is tried. `env`,
`keychain`, and `prompt` select exactly one source and never silently fall back.
`prompt` requires an interactive terminal and never persists the value. Status
checks Keychain item existence without retrieving its password. Resolved keys
from every source are included in raw and terminal-normalized output guards.

On success, stderr prints a receipt before launch (`packet-ask receipt …`) and millisecond phase times after (`packet-ask timing …`). `--json` adds a `timing` object. Neither line contains keys. Receipt paths are JSON escaped, and terminal control sequences are removed from untrusted provider output before it is printed.

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
the conventional signal exit status: SIGHUP is 129 and SIGTERM is 143.

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
