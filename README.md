# accsw

Switch Claude Code and Codex between accounts without ever logging out — and put the desktop apps on
the same account while you are at it.

```
$ accsw
claude: developer — Fable at 20% left
accsw: reopened Claude on developer
codex: staying on stan — 7d at 62% left
```

That is the whole daily surface. Run it and you are on the account with the most left; run it again
and it does nothing, because nothing needed doing.

```
accsw            # go to the best account, and open the apps on it
accsw status     # every account, with what is left and when it comes back
accsw list       # who is captured
accsw add claude # sign in to another account without logging out of this one
accsw add codex
```

## Quota

Numbers are read live, one account at a time, every time you open the picker. Each account's own
stored token queries its own usage, so you see every account's state without switching to it. The
reads are sequential on purpose — a burst of simultaneous authenticated requests for several
different accounts from one address is the shape anti-abuse systems act on.

Claude is chosen on **Fable first, then the 5h window, then the week** — the order in which those
limits actually stop work. A spent *model* window only steers the choice; a spent *unscoped* window
disqualifies the account outright, and its 5h line is hidden because it can read 100% and buy nothing.

- **Claude** — `GET api.anthropic.com/api/oauth/usage`, which reports a `five_hour` and a `seven_day`
  window, each with a utilization percentage and a reset timestamp.
- **Codex** — `GET chatgpt.com/backend-api/wham/usage`, whose `rate_limit.primary_window` and
  `.secondary_window` carry `used_percent`, `limit_window_seconds` and an absolute `reset_at`. Note
  the endpoint: `wham/profiles/me` looks like the obvious candidate and is the wrong one — it returns
  lifetime token stats and no limits at all. The same response also exposes `plan_type`, reset
  credits and per-model limits, which are not surfaced yet.

Windows are labelled from the length each one declares, rather than assuming five hours and a week.

Auto-selection has one rule, and it fits in a sentence: **pick the account whose most-constrained
window is least used.** A tie keeps whatever is already loaded, so it never switches for nothing.
Accounts whose token has expired are reported as such and never chosen — an expired token is
refreshed by switching to that account once. A window past its reset counts as empty, whatever
number was last recorded for it.

`accsw run` applies that rule and then becomes the tool, so you never switch by hand.

One structural limit is worth stating plainly: each tool has exactly one credential slot, so two live
sessions cannot hold different accounts — whichever refreshes its token last wins for both. Claude on
one account and Codex on another is fine; two Claude Code sessions on two accounts is not.

## How it works

The two tools store credentials differently, so `accsw` swaps whatever each one actually reads.

**Claude Code** keeps its OAuth credential in the macOS keychain. The service name is
`Claude Code-credentials`, optionally suffixed with the first eight hex characters of
`sha256(CLAUDE_CONFIG_DIR)` — so a config dir *does* namespace the item, but with that variable unset
there is exactly one global item keyed to your macOS username. A profile parks its blob in its own
keychain item, `accsw-claude-<profile>`, and switching copies it back into the canonical one. The
`oauthAccount` key of `~/.claude.json` is swapped alongside it so the identity metadata matches.

The binary does contain a plaintext `.credentials.json` backend, and it is tempting — but it is
unreachable on macOS: reads try the keychain first, and a successful keychain write *deletes* that
file. It is a degradation mode, not a configuration, so keychain swapping is the only stable path.

**Codex** keeps its credential in `$CODEX_HOME/auth.json`, a plain file. A profile is a copy of that
file, and switching writes it back.

Both mechanisms are on-disk or in-keychain state rather than environment variables, on purpose: IDE
extensions spawn their agent as a native process that never inherits your shell environment, so an
env-var approach would work in the terminal and silently fail in the editor.

## What is deliberately not swapped

Sessions, history, projects, MCP config, plugins. Those are workspace, not identity — you want them
to follow you across accounts, and they weigh about 11 GB. Only the credential and the identity
metadata move.

## Guard rails

Switching warns when processes of that tool are already running, but never blocks. IDE extensions
keep a host process alive for as long as the editor is open, so "is it running" is nearly always yes
— a hard block would only train you to bypass it. The consequence is bounded and worth knowing: a
live session keeps working on its in-memory token, but when it refreshes it rewrites the shared
credential slot, so the next process to start may pick up the account you just switched away from.

Profiles are additive: `save` never touches the live login, and `rm` only forgets a profile — it
never logs anything out.

## Storage

- `~/.config/accsw/registry.json` — profile names, emails, identity metadata (mode 600)
- `~/.config/accsw/codex/<profile>.json` — per-profile Codex credential (mode 600)
- keychain item `accsw-claude-<profile>` — per-profile Claude credential

Override the location with `ACCSW_HOME`.

## How it protects the thing it is holding

Credentials are the whole point, so the handling is deliberate:

- **No diagnostic can leak the payload.** `security` echoes its arguments when it fails, so any of its
  output carrying the credential — or the credential's hex — is dropped rather than interpolated into
  an error message. This is not theoretical: it is how an earlier version printed 8 KB of live
  credential to a terminal.
- **Hex round-trips are decoded.** `security -w` returns hex whenever the stored bytes are not
  printable ASCII. Storing that hex verbatim and writing it back would leave the canonical item
  holding an unparseable string — a bricked login. It is detected and decoded.
- **Writes are verified.** Every keychain write is read back and compared before anything downstream
  is allowed to proceed.
- **Every write is atomic.** Registry, `auth.json` and `~/.claude.json` are written to a temp file
  created at mode 600, fsynced, then `os.replace`d. No truncation window, no world-readable window.
- **Nothing is ever only in the slot.** Every command parks whatever is signed in into a profile
  before doing anything else, so switching is a swap rather than a one-way restore. Without that, a
  profile's stored blob goes stale as its session refreshes, and switching back would write a dead
  refresh token — a browser re-login, which is the exact thing this tool exists to avoid. It also
  means a login made outside accsw is picked up on its own. `ACCSW_ABSORB=0` disables it.
- **Every leg is checked before any byte is written**, so a switch cannot half-apply.
- **A failed keychain read is not "no credential".** Only exit 44 means absent; a locked keychain or
  a denied ACL raises, instead of advising a recapture that would overwrite a good parked blob.
- **Quota reads are sequential**, not a burst of simultaneous authenticated requests for several
  accounts from one address.
- **Redirects are refused.** `urllib` copies headers onto a redirected request without comparing
  hosts, so following one would hand a live access token to whatever host answered.

## Requirements

macOS, Python 3. No third-party packages.

## Tests

```
python3 tests/test_quota.py     # 66 checks — parsing, rollover, hex, naming, selection, display
python3 tests/test_picker.py    # 11 checks — the picker driven through a real pty
python3 tests/test_keychain.py  # 9 checks  — an 8 KB blob round-tripped through the real keychain
```

None touches a real credential: the quota tests replace the HTTP layer with canned payloads shaped
like the real responses, the picker tests run against a throwaway store, and the keychain test writes
a fake blob of realistic size to a throwaway service. That last one exists because size is what broke
an earlier write mechanism — it passed every small-string test, then printed an 8 KB credential to the
terminal the first time it met a real one.
