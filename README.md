# packet-ask

[Korean](README.ko.md)

A local CLI that sends only a **scrubbed packet** to a **SUB** agent. The MAIN agent is whichever session you are in now.

It copies the files or diff you choose, scrubs them, then either runs an official CLI against that packet or prints `packet.md` to paste elsewhere.

> This tool shrinks what you send on purpose. It does not guarantee no leakage and does not stop vendor training. Vendor terms still apply. See [SECURITY.md](SECURITY.md).

MIT licensed. See [LICENSE](LICENSE).

- PyPI: [pypi.org/project/packet-ask](https://pypi.org/project/packet-ask/)
- Repository: [github.com/ictechgy/packet-ask](https://github.com/ictechgy/packet-ask)

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) 0.10.9+ (same lower bound as `uv_build` in `pyproject.toml`)
- GLM / Claude SUB runs: a `claude` CLI on the allowlist path (origin signatures are not verified)
- Kimi runs: a `kimi` CLI on the allowlist path
- `paste` / `grok` / `agy` print a packet and do not launch a vendor

Keys are environment variables only. **This tool does not read `.env` files.** Do not put key values on the command line; they land in shell history. `.env` is gitignored. Variable names are in [.env.example](.env.example). The executable allowlist is in [SECURITY.md](SECURITY.md). `doctor` only checks that help text mentions required flags. It does not prove a no-tools sandbox.

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

## Usage

```bash
packet-ask providers

# Packet only; do not launch a vendor
packet-ask review --provider paste --files src/app.py --question "Find race conditions in this code"

# GLM. Key: PACKET_ASK_GLM_KEY
packet-ask review --provider glm --diff HEAD --question "Review this change"

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

Kimi is official `kimi --quiet` one-shot. It does not open an interactive session. It refuses to run without `PACKET_ASK_KIMI_KEY`. Tools are disabled with a `tools: []` agent file and a non-matching `[tools] enabled` list. `KIMI_CODE_HOME` is only the isolated profile `~/.config/packet-ask/providers/kimi/kimi-code`. Do not run `kimi` in the real repo.

GLM uses the official `claude` binary. **It does not change the parent shell `ANTHROPIC_BASE_URL`.** Only the child environment gets the [Z.ai Claude Code endpoint](https://docs.z.ai/scenario-example/develop-tools/claude) and `PACKET_ASK_GLM_KEY`.

Packet temp dirs live in the OS cache, not the git worktree. cwd is not a sandbox. A `PACKET_ASK_CACHE_DIR` inside the worktree is rejected. `.gitignore` entries for `.packet-ask-tmp/` and `packet.md` only stop leftover files from being committed.

User config `~/.config/packet-ask/providers.toml` adds **paste aliases only**. It does not accept executables, argv, or env.

```toml
version = 1
[providers.gemini]
label = "Gemini CLI"
```

## Skills

`packet-ask install-skills` installs into harness homes. The skill only tells MAIN to call `packet-ask`. Isolation is enforced by the CLI.

## Exit codes

`10`–`14` mean the vendor process was never started.

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

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) Copyright (c) 2026 Coden
