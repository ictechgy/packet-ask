# Security

[Korean](SECURITY.ko.md)

packet-ask shrinks what you send. It does **not** guarantee no leakage and does **not** forbid vendor training.

How personal Kimi Code or GLM Coding Plan subscriptions handle data is defined by each vendor's terms. Using this CLI does not change those terms.

## What this tool does

- It reads only the files or diff you select from the worktree.
- It redacts secret, home-path, email, and phone patterns, then checks again with different patterns. If the re-check fails, the vendor is not started. The list is a denylist and does not catch every secret.
- Packets are created in a dedicated OS cache directory, not the original repo, and deleted on a clean exit. They can remain after a hard kill or crash. Deletion is ordinary file removal, not secure wipe.
- It finds `claude` / `kimi` executables in allowlist directories. They must be owned by the user and not group- or world-writable. **Publish origin and signatures are not verified.**
- Parent `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` are not copied.
- GLM puts the endpoint and `PACKET_ASK_GLM_KEY` only in the child environment, following [Z.ai Claude Code integration](https://docs.z.ai/scenario-example/develop-tools/claude).
- The Kimi API key is passed only as `PACKET_ASK_KIMI_KEY` and is not written to `config.toml`.
- `doctor` checks that required flag names appear in `--help`. It does not prove a no-tools OS sandbox. Launch probes only the selected binary. `doctor` still walks the catalog and caches `--help` by path, mtime, and size for the process lifetime.
- GLM and Claude child environments set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, and `DISABLE_ERROR_REPORTING=1`. The vendor can ignore those flags. This CLI does not delete `~/.claude/projects/`.
- If vendor stdout contains a dedicated key value or is too large, it is discarded and the process exits 22.
- Vendor stdin, stdout, and stderr share one bounded, nonblocking deadline. Explicit file, stdin-question, and git diff collection also stop at configured limits.
- The final rendered `packet.md`, not only its source fragments, must fit `--max-bytes`. Binary and non-UTF-8 explicit files are rejected.
- ANSI CSI/OSC/DCS and unsafe control characters are removed from vendor output. Receipt paths are JSON escaped.
- Tool-owned provider profile directories reject final-component symlinks. A Kimi session cleanup failure is reported rather than silently ignored.

## What this tool does not do

- It does not stop a vendor from training on or storing the packet.
- It does not treat cwd as a sandbox. The SUB CLI cwd is the packet directory.
- It does not send implementation, patch application, or production incident response to a sub.
- It does not run user zsh functions or wrappers at the front of `PATH`. The default allowlist is `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`, `~/.local/bin`, and `PACKET_ASK_*_BIN`.
- It does not prove Claude auto-memory is off. The child env flags are best-effort vendor switches.
- The implementation/incident wording gate is lexical and best-effort; it does not prove semantic intent.

## Keys and profiles

| Variable | Use |
| --- | --- |
| `PACKET_ASK_GLM_KEY` | GLM Coding Plan key. Do not use a global Anthropic key |
| `PACKET_ASK_CLAUDE_KEY` | Anthropic Claude SUB key. Do not use a global Anthropic key |
| `PACKET_ASK_KIMI_KEY` | Kimi key. Not written to disk |
| `PACKET_ASK_CACHE_DIR` | Packet cache parent. Absolute only. Creates a dedicated `packet-ask` child |
| `PACKET_ASK_CLAUDE_BIN` / `PACKET_ASK_KIMI_BIN` | Absolute executable override |
| `PACKET_ASK_BIN_DIRS` | Extra allowlist directories (`os.pathsep`, absolute only) |
| `PACKET_ASK_LANG` | `en` or `ko` for CLI messages |

Do not put keys in the repository, issues, or packets. `.env` and private-key filenames are rejected at collection time.

Isolation profiles under `~/.config/packet-ask/providers/<id>` may remain after a run.

## Reporting vulnerabilities

Do not attach secrets to public issues. Report privately via GitHub Security Advisories: https://github.com/ictechgy/packet-ask/security/advisories

Email: `ictechgy@gmail.com`
