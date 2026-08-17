# accsw

You have several Claude accounts and several ChatGPT accounts. This puts you on whichever one still
has quota left — across the CLIs, the IDE extensions and the desktop apps — without ever logging out.

```
$ accsw
claude: work — Fable at 63% left
codex: staying on personal — 7d at 41% left
```

Run it whenever. If you are already on the best account it does nothing at all.

## Install

macOS, Python 3, nothing else.

```
git clone https://github.com/standujar/accsw.git
ln -s "$PWD/accsw/accsw" ~/.local/bin/accsw
```

## The four commands

```
accsw              go to the account with the most left
accsw status       every account: what is left, when it comes back
accsw list         which accounts are captured
accsw add claude   sign in to another account, without logging out of this one
accsw add codex
```

There is no fifth command on purpose.

## Adding your accounts

Once per account, forever:

```
accsw add claude
```

A browser opens, you sign in, and that account joins the pool. It never touches the account you are
currently using, so you can add one at any time.

Whatever you sign into by hand is picked up too — every run captures the live account before doing
anything else, so an account cannot be lost by forgetting a command.

## How it chooses

**Claude:** the most Fable left. If every account has spent its Fable, the most 5-hour window, then
the most weekly. An account whose weekly or 5-hour window is fully spent is out of the running
entirely — those stop everything, whereas a spent model only stops that model.

**Codex:** the most left, plainly.

The two are chosen independently, because they are two separate logins.

## What follows a switch

| | follows | how |
|---|---|---|
| `claude` CLI | yes | reads the credential accsw swaps |
| Claude Code in Cursor / VSCode | yes | same credential, but the extension host caches it — **reload the window** (`Cmd-Shift-P` → Reload Window) |
| `codex` CLI | yes | reads the file accsw swaps |
| ChatGPT desktop app | yes | same file; accsw reopens it |
| Claude desktop app | **no** | not touched at all — see below |

## The one thing it cannot do

**It does not touch the Claude desktop app.** That app signs in with a web session cookie, issued by
a browser login on claude.ai. What accsw holds is Claude Code's OAuth token — a different credential
from a different flow, and there is no exchange between the two: the app only ever writes
`lastActiveOrg` itself, and the session cookie is set and revoked server-side.

Per-account profiles for it were built and then removed. They worked mechanically and bought nothing:
every profile still needed its own browser sign-in, so switching only ever closed a window to show a
login screen. The app is left exactly where it is.

ChatGPT.app has no such limitation — it reads the same credential file as the CLI, so accsw reopens
it on the new account.

## Where things are kept

```
~/.config/accsw/registry.json          which accounts exist        (0600)
~/.config/accsw/codex/<account>.json   Codex credentials           (0600)
keychain accsw-claude-<account>        Claude credentials
```

## How the credentials are handled

Expiring sign-ins are renewed before use. That matters more than it sounds: renewing **rotates** the
refresh token, so the stored copy and the live one are always written together — leaving either
behind strands the account.

An account is always identified by asking its own credential who it belongs to, never by reading a
neighbouring file. And what is loaded is read from the slot itself, never from the registry, so a
credential changed from outside gets repaired rather than misreported. Both rules exist because their
absence destroyed real accounts during development.

`security` echoes its arguments when it fails, so no diagnostic that contains a credential is ever
printed. Every keychain write is read back and compared, and every file is written to a temp file at
mode 600 and renamed into place.

## Tests

```
python3 tests/test_quota.py      window parsing, ranking, renewal, naming
python3 tests/test_keychain.py   an 8 KB blob round-tripped through the real keychain
```

Neither reaches the network: `ACCSW_OFFLINE=1` makes any real call raise, and both suites set it.

## Knowingly

Owning several accounts you pay for is your business. Rotating between them specifically to get
around a rate limit is what Anthropic's usage policy calls circumventing limits, and they have
enforced it by suspension. This tool makes switching easy; what you switch for is your call.
