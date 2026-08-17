# accsw

Switch Claude Code and the Codex CLI between accounts without ever logging out.

Capture each account once, then flip between them in a second. No re-auth, no device codes,
no browser round-trip.

Run it with no arguments and pick from the menu:

```
$ accsw
switch account   ↑↓ move   ↵ select   a auto   q cancel
 ❯  eliza  ● claude  developer@elizalabs.ai   5h ████░░░░░░  38% resets 2h 10m   7d ███████░░░  71% resets 3d
           ● codex   dev@eliza.ai             5h ██░░░░░░░░  12% resets 45m      7d █░░░░░░░░░   9% resets 5d
    perso  ○ claude  stan@perso.com           5h ░░░░░░░░░░   0% resets 4h       7d ██░░░░░░░░  18% resets 6d
           ○ codex   stan@perso.com           — token expired — switch to this account once to refresh it
```

`●` is the account currently loaded, `○` a captured one waiting. Arrows or `j`/`k` move, `↵` switches,
`a` picks the roomiest account automatically, `q` backs out. Everything is also addressable directly:

```
accsw save perso          # snapshot whoever is logged in right now, call it "perso"
accsw save eliza          # log into the other account once, snapshot it too
accsw use perso           # switch both tools, no menu
accsw use eliza --tool codex   # or just one
accsw auto                # switch to whichever account has the most headroom
accsw status              # who am I right now, with live quota
accsw list                # what's captured
accsw rm perso            # forget a profile (never logs anything out)
```

## Quota

Numbers are read live, per account, in parallel, every time you open the picker. Each account's own
stored token is used to query its own usage, so you see every account's state without switching to it.

- **Claude** — `GET api.anthropic.com/api/oauth/usage`, which reports a `five_hour` and a `seven_day`
  window, each with a utilization percentage and a reset timestamp.
- **Codex** — `GET chatgpt.com/backend-api/wham/profiles/me`, whose `rateLimits.primary` and
  `.secondary` carry `usedPercent`, `windowDurationMins` and `resetsAt`.

Auto-selection has one rule, and it fits in a sentence: **pick the account whose most-constrained
window is least used.** A tie keeps whatever is already loaded, so it never switches for nothing.
Accounts whose token has expired are reported as such and never chosen — an expired token is
refreshed by switching to that account once.

## How it works

The two tools store credentials differently, so `accsw` swaps whatever each one actually reads.

**Claude Code** keeps its OAuth credential in the macOS keychain, service `Claude Code-credentials`,
account = your macOS username. Because the account attribute is the OS user, that item is global:
`CLAUDE_CONFIG_DIR` relocates settings and history but *not* the credential on macOS (on Linux and
Windows it does, via `.credentials.json` — macOS is the odd one out). So a profile parks its blob in
its own keychain item, `accsw-claude-<profile>`, and switching copies it back into the canonical one.
The `oauthAccount` key of `~/.claude.json` is swapped alongside it so the identity metadata matches.

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

`use` refuses to run while a session of that tool is live, because a running session can refresh its
own token and silently put the previous account back. Quit it, or pass `--force` if you know what
you're doing.

Profiles are additive: `save` never touches the live login, and `rm` only forgets a profile — it
never logs anything out.

## Storage

- `~/.config/accsw/registry.json` — profile names, emails, identity metadata (mode 600)
- `~/.config/accsw/codex/<profile>.json` — per-profile Codex credential (mode 600)
- keychain item `accsw-claude-<profile>` — per-profile Claude credential

Override the location with `ACCSW_HOME`.

## Known limitation

Writing a credential back into the keychain goes through `security add-generic-password -w <secret>`,
which puts the secret in that process's argument list for the moment it runs. On a single-user Mac the
only observer is you — any process already running as you could ask the keychain directly anyway. The
airtight fix is to call the Security framework through `ctypes` and never spawn `security` at all;
that is a worthwhile change, but it replaces a well-understood call with code that cannot be exercised
without writing to a real keychain, so it is deliberately left as an open choice rather than shipped
untested.

`~/.claude.json` is rewritten through a temp file and `os.replace`, so an interrupted switch cannot
truncate it.

## Requirements

macOS, Python 3. No third-party packages.

## Tests

```
python3 tests/test_quota.py     # 35 checks — window parsing, formatting, auto-selection
python3 tests/test_picker.py    # 11 checks — the picker driven through a real pty
```

Neither touches a real credential: the quota tests replace the HTTP layer with canned payloads shaped
like the real responses, and the picker tests run against a throwaway store.
