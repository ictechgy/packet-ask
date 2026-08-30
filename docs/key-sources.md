# Credential sources

`packet-ask` resolves provider credentials through an explicit, bounded set of
sources. It never searches another application's settings, arbitrary files, or
user-defined commands.

## Sources

- `auto` (task CLI default): use the dedicated environment variable first,
  then the packet-ask-owned macOS Keychain item. It never opens packet-ask's
  one-run secret prompt, though macOS may still request Keychain approval.
- `env`: use only `PACKET_ASK_<PROVIDER>_KEY`.
- `keychain`: use only the canonical packet-ask macOS Keychain item.
- `prompt`: read once with a no-echo terminal prompt and keep the value only in
  process memory. It never persists the value.

Canonical Keychain items use the current macOS account and service names
`packet-ask-glm`, `packet-ask-kimi`, and `packet-ask-claude`. The CLI does not
read ZCode, Claude Code, shell-profile, `.env`, or password-manager storage.
Those tools can still inject the dedicated environment variable explicitly.

## Commands

```text
packet-ask credentials status [glm|kimi|claude]
packet-ask credentials set <provider> --store macos-keychain --access command
packet-ask review --provider glm --credential-source auto ...
packet-ask review --provider glm --credential-source keychain ...
packet-ask review --provider glm --credential-source prompt ...
```

`credentials set` delegates secret entry to `/usr/bin/security` with its
interactive `-w` prompt. The key is not placed in packet-ask argv, stdout,
stderr, or shell history. Noninteractive calls fail instead of waiting.

`--access` is required so the user must choose the Keychain threat model.
`--access command` trusts the fixed `/usr/bin/security` binary so background
agents can retrieve the item without a GUI approval. It
protects the key at rest but is not a boundary against another process already
running as the same user, because that process can invoke `/usr/bin/security`.
`--access prompt` stores the item with no trusted application (`-T ""`) and asks
macOS for approval on each password read. That mode can fail in headless or
background sessions where the approval UI is unavailable.

After a successful `command` save, packet-ask immediately reads the canonical
item back without printing it. This validates the stored value shape and gives
a best-effort check that the requested trusted-application ACL is usable. An
interactive macOS approval can make this check pass even when a later headless
session cannot show that approval, so runtime retrieval remains fail-closed.

## Security invariants

- Environment variables win in `auto`; other stores are not silently mixed.
- An explicit source never falls back to another source.
- Missing or inaccessible credentials fail before the provider process starts.
- Status reports existence only and never retrieves or prints a key.
- The exact resolved key is included in provider-output reflection checks, both
  before and after terminal-control removal.
- Key values never enter receipts, timing, JSON metadata, errors, or config.
- `--key`, `--key-file`, arbitrary key commands, and automatic third-party
  config discovery remain unsupported.
