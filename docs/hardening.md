# packet-ask hardening design

This batch tightens existing confinement promises. It does not add a provider or
broaden the tasks that a SUB may perform.

## Resource boundaries

- `--max-bytes` limits the final UTF-8 `packet.md`, including framing and file
  names. Collection also stops reading as soon as its input budget is exceeded.
- Explicit files, question stdin, git name-status, and git diff output are read
  with bounds. Diff scope also observes `--max-files`.
- Vendor stdin, stdout, and stderr are multiplexed under one deadline. A child
  cannot suspend the timeout by refusing to read stdin while filling stdout.
- Binary explicit files are rejected instead of being silently decoded with
  replacement characters.

## Terminal and filesystem boundaries

- Vendor output is checked in both raw and terminal-control-stripped forms.
  ANSI CSI/OSC/DCS sequences and unsafe control characters never reach the
  terminal.
- Receipt paths are JSON escaped so a crafted filename cannot create terminal
  lines or control sequences.
- Tool-owned provider directories and files reject final-component symlinks.
  Kimi session cleanup failures are reported instead of silently ignored.
- Skill installation rejects symlinks in every destination component below the
  selected harness home.

## Policy and redaction

- The implementation-request gate covers common English and Korean imperative
  forms. It remains a lexical, best-effort guard; no regex can prove intent.
- Assignment redaction remains literal-only. A small quoted-string scanner
  handles escaped quote characters without deleting expressions or identifiers.
- English remains the default for user-visible CLI text; Korean is selected with
  `PACKET_ASK_LANG=ko`.
- Independent verification builds a Unicode detection shadow after normal
  redaction. It canonicalizes compatibility forms, format controls, dot/dash
  variants, and decimal digits to detect international mailbox and phone
  leftovers without changing source text or public redaction counts.
- Unicode mailbox candidates with an ASCII TLD use a conservative likely-TLD
  gate when either operand is non-ASCII. This preserves attribute-like Unicode
  matrix expressions while still rejecting common and IDNA email forms.
- Known token-family regexes are shared by primary scrub and shadow verification
  so Cf, variation-selector, filler, and compatibility forms cannot bypass both.
  Secret literals, URL userinfo, and PEM headers use the shadow verifier too.
- Dotted Korean mobile numbers are scrubbed when canonical and mixed separators
  fail closed after bounded normalization. General E.164 matching remains out of
  scope until its false-positive budget is measured.

## Delivery controls

- CI actions use immutable commit SHAs and tests run on the minimum and newest
  declared Python versions.
- Optional gitleaks remains off by default. Executable Grok/Antigravity and a
  `--safe-mode` switch remain out of scope until their no-tools and login
  behavior are measured independently.
