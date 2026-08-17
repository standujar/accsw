# accsw

Switch Claude Code and the Codex CLI between accounts without ever logging out.

Capture each account once, then flip between them in a second. No re-auth, no device codes,
no browser round-trip.

Run it with no arguments and pick from the menu:

```
$ accsw
switch account   ↑↓ move   ↵ select   a auto   q cancel
 ❯  eliza  ● claude  developer@elizalabs.ai     5h ████░░   62% 2h    7d ███░░░   29% 3d
           ● codex   dev@eliza.ai               5h █░░░░░   12% 45m   7d ░░░░░░    9% 5d
    perso  ○ claude  stan@perso.com             5h ██████  100% 4h    7d █████░   82% 6d
           ○ codex   stan@perso.com             — token expired — switch to this account once to refresh it
```

Percentages are what is **left**. `●` is the account currently loaded, `○` a captured one waiting.
Arrows or `j`/`k` move, `↵` switches, `a` picks the roomiest account automatically, `q` backs out.

Or never think about it at all — launch through `run` and it lands on the roomiest account:

```
accsw run claude          # picks the account with the most left, switches, launches claude
accsw run codex exec "…"  # arguments pass straight through
```

Worth aliasing: `alias claude='accsw run claude'`.

```
accsw save                # capture whoever is signed in, named after their email
accsw save perso          # ...or name it yourself
accsw use perso           # switch both tools, no menu
accsw use eliza --tool codex   # or just one
accsw auto                # switch to whichever account has the most headroom
accsw auto --tool codex   # ...judged on Codex quota alone
accsw status              # who am I right now, with live quota
accsw list                # what's captured
accsw save perso --replace  # rebind a profile to a different account, on purpose
accsw rm perso            # forget a profile (never logs anything out)
```

## Quota

Numbers are read live, one account at a time, every time you open the picker. Each account's own
stored token queries its own usage, so you see every account's state without switching to it. The
reads are sequential on purpose — a burst of simultaneous authenticated requests for several
different accounts from one address is the shape anti-abuse systems act on.

- **Claude** — `GET api.anthropic.com/api/oauth/usage`, which reports a `five_hour` and a `seven_day`
  window, each with a utilization percentage and a reset timestamp.
- **Codex** — no free source exists. `wham/profiles/me` returns lifetime stats, not limits, and the
  real numbers (`RateLimitWindow { used_percent, window_minutes, resets_at }`) reach the client as a
  streamed event *during a turn*. Reading them would mean paying for a completion, so accsw says so
  instead of doing it behind your back. Auto-selection simply has no Codex signal to act on.

Auto-selection has one rule, and it fits in a sentence: **pick the account whose most-constrained
window is least used.** A tie keeps whatever is already loaded, so it never switches for nothing.
Accounts whose token has expired are reported as such and never chosen — an expired token is
refreshed by switching to that account once. A window past its reset counts as empty, whatever
number was last recorded for it.

`accsw run` applies that rule and then becomes the tool, so you never switch by hand. It will not
switch while a session of that tool is already running, and that is a hard constraint rather than
caution: each tool has exactly one credential slot, so two live sessions cannot hold different
accounts — whichever refreshes its token last would win for both.

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

- **Never in an argument list.** Writes go to `security -i` over stdin, hex-encoded with `-X`, so no
  secret ever appears in `ps`.
- **Hex round-trips are decoded.** `security -w` returns hex whenever the stored bytes are not
  printable ASCII. Storing that hex verbatim and writing it back would leave the canonical item
  holding an unparseable string — a bricked login. It is detected and decoded.
- **Every write is atomic.** Registry, `auth.json` and `~/.claude.json` are written to a temp file
  created at mode 600, fsynced, then `os.replace`d. No truncation window, no world-readable window.
- **Switching is a swap, not a restore.** Before loading a profile, the credential currently live is
  parked back into the profile it came from. Without that, a profile's stored blob goes stale as its
  session refreshes, and switching back would write a dead refresh token — a browser re-login, which
  is the exact thing this tool exists to avoid.
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
