# Security

[Korean](SECURITY.ko.md)

packet-ask shrinks what you send. It does **not** guarantee no leakage and does **not** forbid vendor training.

How personal Kimi Code or GLM Coding Plan subscriptions handle data is defined by each vendor's terms. Using this CLI does not change those terms.

## What this tool does

- It reads only the files or diff you select from the worktree.
- `inspect review|research` runs the same scope, redaction, packet verification, and cleanup without loading a provider or credential and outputs only fixed public metadata.
- It redacts secret, home-path, email, and phone patterns, then checks again with different patterns. If the re-check fails, the vendor is not started. The list is a denylist and does not catch every secret.
- The re-check uses a detection-only Unicode shadow for NFKC compatibility forms, format controls, equivalent dots/dashes, Unicode decimal digits, international mailbox labels, and phone candidates. The packet is never rewritten from the shadow; suspicious leftovers fail closed. Ambiguous Unicode matrix syntax with an unknown ASCII attribute-like suffix is allowed to limit source-code false positives.
- Known token families are checked symmetrically in primary scrub and shadow verification. Secret literals, URL userinfo, and PEM headers also use the shadow. Canonical dotted Korean mobile numbers are scrubbed; mixed dot/dash/space forms fail closed. This does not claim general E.164 coverage.
- Packets are created in a dedicated OS cache directory, not the original repo, and deleted on a clean exit. New packets hold a private directory advisory lock and a 0600 lease marker. A later run removes data from current-user, 0700 packet directories only when their directory lock is available and marker is at least 24 hours old; active, fresh, symlinked, non-private, and legacy directories without a marker are skipped. A hard kill can therefore leave data for at least 24 hours and until a later run performs cleanup. Deletion is ordinary file removal, not secure wipe.
- It finds `claude` / `kimi` executables in allowlist directories. They must be owned by the user and not group- or world-writable. **Publish origin and signatures are not verified.**
- Parent `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` are not copied.
- GLM puts the endpoint and resolved dedicated GLM credential only in the child environment, following [Z.ai Claude Code integration](https://docs.z.ai/scenario-example/develop-tools/claude).
- The resolved Kimi credential is passed only in the child environment and is not written to `config.toml`.
- Credential sources are explicit: dedicated environment, packet-ask-owned macOS Keychain item, or a one-run no-echo prompt. `auto` uses environment first and then canonical Keychain; it never prompts.
- Credential resolution uses an immutable builtin backend registry. `auto` is fixed to environment then Keychain; users cannot register a backend, key command, key file, executable, or third-party settings adapter.
- Keychain access uses fixed `/usr/bin/security` argv without a shell and a minimal environment. Status checks existence without retrieving a password; `credentials set` lets `security -w` prompt directly so the key is absent from argv and shell history.
- Keychain `--access command` trusts `/usr/bin/security` for background-agent use and protects the key at rest, but it is not a boundary against another process with the same user authority. `--access prompt` trusts no application and may be unusable in a headless session.
- `doctor` checks that required flag names appear in `--help`. It does not prove a no-tools OS sandbox. Help probes have one deadline, a combined output cap, and process-group termination. Launch probes only the selected binary. `doctor` still walks the catalog and caches successful `--help` by path, mtime, and size for the process lifetime.
- Builtin launchers and doctor probe kinds are selected by an immutable code registry. User aliases have no adapter ID and cannot register or select executables, argv, env, launchers, probes, or hooks.
- GLM and Claude child environments set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, and `DISABLE_ERROR_REPORTING=1`. The vendor can ignore those flags. This CLI does not delete `~/.claude/projects/`.
- If vendor stdout contains a dedicated key value or is too large, it is discarded and the process exits 22.
- Vendor stdin, stdout, and stderr share one bounded, nonblocking deadline. Explicit file, stdin-question, and git diff collection also stop at configured limits.
- The final rendered `packet.md`, not only its source fragments, must fit `--max-bytes`. Binary and non-UTF-8 explicit files are rejected.
- ANSI CSI/OSC/DCS and unsafe control characters are removed from vendor output. Receipt paths are JSON escaped.
- Tool-owned provider profile directories reject final-component symlinks. A Kimi session cleanup failure is reported rather than silently ignored.
- Successful Kimi output is withheld until session cleanup succeeds. If a provider, output-guard, or signal failure already exists, a simultaneous Kimi cleanup failure emits only a fixed non-sensitive warning and cannot replace the primary failure.
- Kimi config, execution, and session cleanup share a 0600 non-inheritable advisory run lock. Lock acquisition is bounded to 30 seconds; a competing run fails before mutating `KIMI_CODE_HOME` or launching Kimi.
- Worktree discovery, diff collection, and packet-local Git initialization use one bounded runner with process-group termination on timeout, output excess, and interrupts. Task-scoped SIGTERM/SIGHUP handlers defer signal delivery until a spawned process group or built packet is registered, then reuse the same child and packet cleanup paths.
- Success output is withheld until the temporary packet is removed. Cleanup failure cannot replace an existing provider failure code.
- Receipt and manifest redaction metadata use an exact allowlist of non-negative integer counters; internal report fields are never serialized.
- JSON failures use fixed code/kind/message mappings. They never serialize raw argv, exception text, paths, credentials, provider stderr, or tracebacks.

## What this tool does not do

- It does not stop a vendor from training on or storing the packet.
- It does not treat cwd as a sandbox. The SUB CLI cwd is the packet directory.
- It does not send implementation, patch application, or production incident response to a sub.
- It does not run user zsh functions or wrappers at the front of `PATH`. The default allowlist is `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin`, `~/.local/bin`, and `PACKET_ASK_*_BIN`.
- It does not prove Claude auto-memory is off. The child env flags are best-effort vendor switches.
- The implementation/incident wording gate is lexical and best-effort; it does not prove semantic intent.
- It does not inspect ZCode, Claude Code, `.env`, arbitrary key files, password-manager stores, or user-defined key commands. External managers must inject the dedicated environment variable.

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

Canonical macOS Keychain services are `packet-ask-glm`, `packet-ask-kimi`, and
`packet-ask-claude`, under the current uid's account name. Every resolved key,
including Keychain and prompt values, is checked against provider output before
and after terminal-control removal. See [docs/key-sources.md](docs/key-sources.md).

Do not put keys in the repository, issues, or packets. `.env` and private-key filenames are rejected at collection time.

Isolation profiles under `~/.config/packet-ask/providers/<id>` may remain after a run.

## Reporting vulnerabilities

Do not attach secrets to public issues. Report privately via GitHub Security Advisories: https://github.com/ictechgy/packet-ask/security/advisories

Email: `ictechgy@gmail.com`
