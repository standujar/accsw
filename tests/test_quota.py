#!/usr/bin/env python3
"""Unit tests for the quota layer: window parsing, formatting, auto-selection.

No network: the HTTP layer is replaced with canned payloads shaped like the real
responses (Claude /api/oauth/usage, Codex wham/profiles/me).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import time
from pathlib import Path

os.environ["ACCSW_OFFLINE"] = "1"  # no test may reach the network

ACCSW = Path(__file__).resolve().parent.parent / "accsw"

loader = importlib.machinery.SourceFileLoader("accsw", str(ACCSW))
spec = importlib.util.spec_from_loader("accsw", loader)
accsw = importlib.util.module_from_spec(spec)
loader.exec_module(accsw)

HOUR = 3600
DAY = 86400

results: list[bool] = []


def check(name: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  got={got!r} want={want!r}"))


def check_that(name: str, condition: bool, detail: str = "") -> None:
    results.append(condition)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))


print("parse_epoch normalises every unit the APIs use")
check("epoch seconds pass through", accsw.parse_epoch(1786977875), 1786977875)
check("epoch millis are divided", accsw.parse_epoch(1786977875791), 1786977875)
check("ISO-8601 with Z", accsw.parse_epoch("2026-08-17T21:00:00Z") > 1786000000, True)
check("garbage string is None", accsw.parse_epoch("soon"), None)
check("None is None", accsw.parse_epoch(None), None)

print("humanize_reset reads like a human wrote it")
now = int(time.time()) + 30  # off any minute boundary, so the clock cannot tick into the assertion
check("past resets say now", accsw.humanize_reset(int(time.time()) - 10), "now")
check("minutes only", accsw.humanize_reset(now + 25 * 60), "25m")
check("hours and minutes", accsw.humanize_reset(now + 2 * HOUR + 10 * 60), "2h 10m")
check("whole hours drop minutes", accsw.humanize_reset(now + 3 * HOUR), "3h")
check("days and hours", accsw.humanize_reset(now + 2 * DAY + 4 * HOUR), "2d 4h")
check("whole days drop hours", accsw.humanize_reset(now + 5 * DAY), "5d")
check("unknown reset is blank", accsw.humanize_reset(None), "")

print("bar renders proportionally and clamps")
check("empty", accsw.bar(0), "░" * 10)
check("half", accsw.bar(50), "█" * 5 + "░" * 5)
check("full", accsw.bar(100), "█" * 10)
check("over 100 clamps", accsw.bar(140), "█" * 10)
check("negative clamps", accsw.bar(-5), "░" * 10)

print("claude reads the limits array, including model-scoped windows")
accsw.http_get_json = lambda url, token: {
    "five_hour": {"utilization": 1.0, "resets_at": None},
    "limits": [
        {"kind": "session", "percent": 1, "resets_at": now + 2 * HOUR, "scope": None},
        {"kind": "weekly_all", "percent": 52, "resets_at": now + 3 * DAY, "scope": None},
        {
            "kind": "weekly_scoped",
            "percent": 100,
            "severity": "critical",
            "resets_at": now + 3 * DAY,
            "scope": {"model": {"display_name": "Fable"}},
            "is_active": True,
        },
    ],
}
windows = accsw.claude_quota("token")
check("unscoped windows get short names", [w[0] for w in windows][:2], ["5h", "7d"])
check("the scoped one is named after its model", windows[2][0], "Fable")

print("a model-scoped limit is what auto-select must see")
registry_scoped = {"profiles": {"fresh": {"claude": {}}, "maxed": {"claude": {}}}, "active": {}}
scoped = {
    ("fresh", "claude"): [("5h", 10.0, None), ("Fable", 20.0, None)],
    ("maxed", "claude"): [("5h", 1.0, None), ("Fable", 100.0, None)],
}
check("looks-fresh-but-maxed is rejected", accsw.best_profile(registry_scoped, scoped, "claude")[0], "fresh")

print("windows are named by their own declared length")
check("five hours", accsw.window_label(5 * HOUR), "5h")
check("one week", accsw.window_label(604800), "7d")
check("one day", accsw.window_label(DAY), "1d")
check("missing length is unknown", accsw.window_label(None), "?")

print("codex payloads map to windows (wham/usage shape)")
accsw.http_get_json = lambda url, token: {
    "plan_type": "pro",
    "rate_limit": {
        "allowed": False,
        "limit_reached": True,
        "primary_window": {
            "used_percent": 100,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 307744,
            "reset_at": now + 3 * DAY,
        },
        "secondary_window": None,
    },
}
windows = accsw.codex_quota("token")
check("only the reported window", [label for label, _, _ in windows], ["7d"])
check("exhausted account reads 100 used", windows[0][1], 100.0)

print("a payload with no rate_limit yields no windows rather than a wrong gauge")
accsw.http_get_json = lambda url, token: {"plan_type": "pro"}
check("no windows", accsw.codex_quota("token"), [])

print("claude is chosen on Fable first, then 5h, then the week")
prio = {"profiles": {"a": {"claude": {}}, "b": {"claude": {}}}, "active": {}}
fable = {
    ("a", "claude"): [("Fable", 60.0, None), ("5h", 90.0, None), ("7d", 10.0, None)],
    ("b", "claude"): [("Fable", 90.0, None), ("5h", 1.0, None), ("7d", 1.0, None)],
}
check("most Fable left wins despite a worse week", accsw.best_profile(prio, fable, "claude")[0], "a")
tied = {
    ("a", "claude"): [("Fable", 100.0, None), ("5h", 70.0, None), ("7d", 10.0, None)],
    ("b", "claude"): [("Fable", 100.0, None), ("5h", 20.0, None), ("7d", 5.0, None)],
}
check("all Fable spent falls through to the 5h window",
      accsw.best_profile(prio, tied, "claude")[0], "b")
check("the key is Fable, 5h, week in that order",
      accsw.selection_key([("7d", 10.0, None), ("Fable", 40.0, None)], "claude"),
      (60.0, 100.0, 90.0))
check("codex compares on its single window",
      accsw.selection_key([("7d", 30.0, None)], "codex"), (70.0,))

print("the offline guard makes a forgotten stub fail loudly")
# a fresh module, so the stubs installed above cannot mask the real function
raw_loader = importlib.machinery.SourceFileLoader("accsw_raw", str(ACCSW))
pristine = importlib.util.module_from_spec(importlib.util.spec_from_loader("accsw_raw", raw_loader))
raw_loader.exec_module(pristine)
try:
    pristine.http_get_json("https://api.anthropic.com/api/oauth/usage", "not-a-token")
    check_that("refuses to reach the network", False, "it made a call")
except pristine.Fail as error:
    check_that("refuses to reach the network", "offline" in str(error), str(error))

print("a credential names itself; a neighbouring file never speaks for it")
accsw.http_get_json = lambda url, token: {
    "account": {"email": "real@owner.com", "uuid": "u-1"},
    "organization": {"uuid": "o-1", "name": "Org", "organization_type": "claude_max"},
}
blob = '{"claudeAiOauth": {"accessToken": "tok"}}'
check("claude asks the API who this credential is",
      accsw.credential_owner("claude", blob), "real@owner.com")
check("and keeps the account metadata with it",
      accsw.claude_oauth_identity("tok")["organizationType"], "claude_max")

import base64 as _b64, json as _json
claims = _b64.urlsafe_b64encode(_json.dumps({"email": "codex@owner.com"}).encode()).decode().rstrip("=")
codex_blob = _json.dumps({"tokens": {"id_token": f"h.{claims}.s"}})
check("codex reads the address out of the credential it is filing",
      accsw.credential_owner("codex", codex_blob), "codex@owner.com")

print("the 5h line is dropped when the week is spent, since it can buy nothing")
spent_week = [("5h", 0.0, None), ("7d", 100.0, None), ("Fable", 100.0, None)]
labels = [line.split()[0] for line in accsw.quota_lines(spent_week, False)]
check("5h is hidden behind a spent week", labels, ["7d", "Fable"])
open_week = [("5h", 0.0, None), ("7d", 10.0, None)]
check("and kept when the week still has room",
      [line.split()[0] for line in accsw.quota_lines(open_week, False)], ["5h", "7d"])

print("a spent weekly window disqualifies; a spent model does not")
block = {"profiles": {"open": {"claude": {}}, "weekly": {"claude": {}}}, "active": {}}
cases = {
    ("open", "claude"): [("5h", 90.0, None), ("7d", 90.0, None), ("Fable", 100.0, None)],
    ("weekly", "claude"): [("5h", 0.0, None), ("7d", 100.0, None), ("Fable", 0.0, None)],
}
check("a spent week loses to a spent model", accsw.best_profile(block, cases, "claude")[0], "open")
check("only unscoped windows block",
      [label for label, _ in accsw.blocking([("Fable", 100.0, None), ("5h", 100.0, None)])], ["5h"])
check("a spent model alone blocks nothing", accsw.blocking([("Fable", 100.0, None)]), [])

print("what is loaded is read from the slot, never from the record")
import tempfile as _tmp, pathlib as _pl
store = _pl.Path(_tmp.mkdtemp())
accsw.CODEX_VAULT = store / "codex"
accsw.CODEX_VAULT.mkdir(parents=True)
accsw.CODEX_AUTH = store / "auth.json"
reg_live = {"profiles": {"one": {"codex": {}}, "two": {"codex": {}}}, "active": {"codex": "one"}}
(accsw.CODEX_VAULT / "one.json").write_text('{"tokens": {"id_token": "a"}}')
(accsw.CODEX_VAULT / "two.json").write_text('{"tokens": {"id_token": "b"}}')
check("an empty slot loads nothing, whatever the record says",
      accsw.actually_loaded("codex", reg_live), None)
accsw.CODEX_AUTH.write_text('{"tokens": {"id_token": "b"}}')
check("the slot names its real owner", accsw.actually_loaded("codex", reg_live), "two")

print("a newer parked sign-in is never overwritten by an older live one")
fresh = '{"claudeAiOauth": {"expiresAt": %d}}' % ((time.time() + 7200) * 1000)
stale = '{"claudeAiOauth": {"expiresAt": %d}}' % ((time.time() + 60) * 1000)
check("claude: the later one wins", accsw.fresher("claude", fresh, stale), True)
check("claude: and not the other way round", accsw.fresher("claude", stale, fresh), False)
check("an unreadable expiry never wins",
      accsw.fresher("claude", '{"claudeAiOauth": {}}', stale), False)
check("a null oauth object does not explode",
      accsw.claude_token_expiry('{"claudeAiOauth": null}'), None)

print("atomic_write leaves the target intact and never widens it")
import tempfile as _t, os as _os, pathlib as _p
scratch = _p.Path(_t.mkdtemp())
target = scratch / "cred.json"
accsw.atomic_write(target, '{"a": 1}')
check("written", target.read_text(), '{"a": 1}')
check("mode 600 from creation", oct(target.stat().st_mode)[-3:], "600")
accsw.atomic_write(target, '{"a": 2}')
check("replaced in place", target.read_text(), '{"a": 2}')
check("no staged file left behind", [f.name for f in scratch.iterdir()], ["cred.json"])

print("codex sign-ins are renewed rather than left to expire")
check("a credential with no refresh token cannot be renewed",
      accsw.refresh_codex('{"tokens": {"id_token": "x"}}'), None)
try:
    accsw.refresh_codex('{"tokens": {"refresh_token": "r"}}')
    check_that("renewal obeys the offline guard", False, "it made a call")
except accsw.Fail as error:
    check_that("renewal obeys the offline guard", "offline" in str(error), str(error))
check("an id_token an hour old is treated as due",
      accsw.codex_token_expiry('{"tokens": {"id_token": "not.a.jwt"}}'), None)

print("claude sign-ins are renewed on the same rule as codex")
check("no refresh token means nothing to renew",
      accsw.refresh_claude('{"claudeAiOauth": {"accessToken": "a"}}'), None)
try:
    accsw.refresh_claude('{"claudeAiOauth": {"refreshToken": "r", "scopes": ["user:profile"]}}')
    check_that("claude renewal obeys the offline guard", False, "it made a call")
except accsw.Fail as error:
    check_that("claude renewal obeys the offline guard", "offline" in str(error), str(error))
check("expiry is read from expiresAt in milliseconds",
      round(accsw.claude_token_expiry('{"claudeAiOauth": {"expiresAt": %d}}' % ((time.time() + 600) * 1000)) / 60),
      10)
check("a blob with no expiry has none", accsw.claude_token_expiry('{"claudeAiOauth": {}}'), None)

print("best_profile picks the most headroom")
registry = {
    "profiles": {
        "perso": {"claude": {"email": "p@x"}, "codex": {"email": "p@x"}},
        "eliza": {"claude": {"email": "e@x"}, "codex": {"email": "e@x"}},
    },
    "active": {"claude": "eliza", "codex": "eliza"},
}
quota = {
    ("perso", "claude"): [("7d", 20.0, None)],
    ("perso", "codex"): [("7d", 10.0, None)],
    ("eliza", "claude"): [("7d", 80.0, None)],
    ("eliza", "codex"): [("7d", 5.0, None)],
}
name, why = accsw.best_profile(registry, quota, "claude")
check("picks the roomier account", name, "perso")
check_that("names the binding window", why == "7d at 80% left", why)

print("best_profile honours --tool instead of mixing both")
tool_split = {
    ("perso", "claude"): [("7d", 90.0, None)],
    ("perso", "codex"): [("7d", 5.0, None)],
    ("eliza", "claude"): [("7d", 10.0, None)],
    ("eliza", "codex"): [("7d", 95.0, None)],
}
check("codex-only picks perso", accsw.best_profile(registry, tool_split, "codex")[0], "perso")
check("claude-only picks eliza", accsw.best_profile(registry, tool_split, "claude")[0], "eliza")

print("a tie keeps the account already loaded")
quota_tied = {
    ("perso", "claude"): [("7d", 50.0, None)],
    ("perso", "codex"): [("7d", 50.0, None)],
    ("eliza", "claude"): [("7d", 50.0, None)],
    ("eliza", "codex"): [("7d", 50.0, None)],
}
check("no pointless switch", accsw.best_profile(registry, quota_tied, "claude")[0], "eliza")

print("accounts with no usable data are skipped, not chosen")
quota_broken = {
    ("perso", "claude"): accsw.Fail("token expired"),
    ("perso", "codex"): accsw.Fail("token expired"),
    ("eliza", "claude"): [("7d", 99.0, None)],
    ("eliza", "codex"): [("7d", 99.0, None)],
}
check("prefers the account it could measure", accsw.best_profile(registry, quota_broken, "claude")[0], "eliza")

print("an account we could read beats one we could not, even when it is tight")
reg2 = {"profiles": {"tight": {"claude": {}}, "unknown": {"claude": {}}}, "active": {}}
tiers = {("tight", "claude"): [("Fable", 100.0, None)], ("unknown", "claude"): accsw.Fail("HTTP 429")}
check("unreadable never wins", accsw.best_profile(reg2, tiers, "claude")[0], "tight")
check("roomy still beats tight",
      accsw.best_profile(reg2, {("tight", "claude"): [("Fable", 10.0, None)],
                                ("unknown", "claude"): accsw.Fail("x")}, "claude")[0], "tight")

print("an unreadable account is ranked last, not discarded")
name, why = accsw.best_profile(registry, {}, "claude")
check_that("still returns a candidate", name in ("perso", "eliza"), repr(name))
check_that("says the token refreshes on use", "refreshes on first use" in why, why)
check("no captured profile for the tool returns nothing",
      accsw.best_profile({"profiles": {}, "active": {}}, {}, "claude")[0], None)

print("a window past its reset has rolled over, whatever the stored number said")
check("future window keeps its number", accsw.effective_percent(38.0, now + HOUR), 38.0)
check("expired window reads as empty", accsw.effective_percent(99.0, now - 60), 0.0)
check("unknown reset keeps its number", accsw.effective_percent(38.0, None), 38.0)

print("failures are surfaced verbatim")
check("error text shown", accsw.quota_lines(accsw.Fail("token expired"), False), ["— token expired"])

print("the display average is the mean of what constrains each tool")
entry_both = {"claude": {}, "codex": {}}
avg_quota = {
    ("acc", "claude"): [("5h", 20.0, None), ("Fable", 40.0, None)],
    ("acc", "codex"): [("7d", 50.0, None)],
}
# claude is constrained by Fable at 60% left, codex by its week at 50% -> 55
check("mean of the deciding windows", accsw.availability("acc", entry_both, avg_quota), 55.0)
check("a tool that answered nothing is left out of the mean",
      accsw.availability("acc", entry_both, {("acc", "codex"): [("7d", 10.0, None)]}), 90.0)
check("nothing readable means no figure at all",
      accsw.availability("acc", entry_both, {}), None)

print("a caller-given width aligns windows whose labels differ in length")
narrow = accsw.quota_lines([("7d", 10.0, None)], False, 5)
check("padded to the wider label", narrow[0].startswith("7d    "), True)
check("and left alone without one", accsw.quota_lines([("7d", 10.0, None)], False)[0].startswith("7d █"), True)

print("status lines are aligned, one per window, and colour-free when not a terminal")
lines = accsw.quota_lines([("5h", 38.0, now + 2 * HOUR), ("7d", 90.0, now + 3 * DAY)], False)
check("one line per window", len(lines), 2)
check("first line", lines[0], "5h ██████░░░░  62% left   resets in 2h")
check("second line", lines[1], "7d █░░░░░░░░░  10% left   resets in 3d")
check_that("no escape codes without a terminal", ESC not in "".join(lines) if (ESC := "\x1b") else False)
check("a just-reset window says so", accsw.quota_lines([("7d", 90.0, None)], False)[0].endswith("just reset"), True)

print("profiles are named after the account, and a name is never stolen")
check("simple local part", accsw.slugify("developer"), "developer")
check("dots and plus become dashes", accsw.slugify("first.last+work"), "first-last-work")
check("collapses runs and trims", accsw.slugify("--Stan__Test--"), "stan-test")

empty = {"profiles": {}, "active": {}}
check("named from the local part",
      accsw.name_for("you@work.example", "claude", empty), "you")

taken = {"profiles": {"stan": {"claude": {"email": "stan@gmail.com"}}}, "active": {}}
check("a name held by another account is qualified by domain",
      accsw.name_for("stan@outlook.com", "claude", taken), "stan-outlook")
check("the same account reuses its own profile",
      accsw.name_for("stan@gmail.com", "claude", taken), "stan")


print("colour tracks how much is left")
check("plenty is green", accsw.remaining_colour(80.0), "32")
check("half is green", accsw.remaining_colour(50.0), "32")
check("getting low is amber", accsw.remaining_colour(35.0), "33")
check("nearly out is red", accsw.remaining_colour(5.0), "31")
check_that("colour is applied when enabled", "\x1b[32m" in accsw.quota_lines([("5h", 10.0, None)], True)[0])
check("paint is inert when disabled", accsw.paint("x", "32", False), "x")

print("hex detection guards the keychain round-trip")
check("json is not hex", accsw.looks_hex('{"a":1}'), False)
check("even-length lowercase hex", accsw.looks_hex("7b2261223a317d0a"), True)
check("odd length is not hex", accsw.looks_hex("7b2261223a317d0"), False)
check("empty is not hex", accsw.looks_hex(""), False)
check("uppercase is not hex", accsw.looks_hex("7B2261223A317D0A"), False)
check(
    "hex decodes back to the original blob",
    bytes.fromhex('{"a":1}\n'.encode().hex()).decode(),
    '{"a":1}\n',
)

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
