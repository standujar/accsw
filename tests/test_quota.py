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
now = int(time.time())
check("past resets say now", accsw.humanize_reset(now - 10), "now")
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
check("a maxed model leaves no headroom", accsw.headroom(windows), 0.0)

print("a model-scoped limit is what auto-select must see")
registry_scoped = {"profiles": {"fresh": {"claude": {}}, "maxed": {"claude": {}}}, "active": {}}
scoped = {
    ("fresh", "claude"): [("5h", 10.0, None), ("Fable", 20.0, None)],
    ("maxed", "claude"): [("5h", 1.0, None), ("Fable", 100.0, None)],
}
check("looks-fresh-but-maxed is rejected", accsw.best_profile(registry_scoped, scoped)[0], "fresh")

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
check("no headroom left", accsw.headroom(windows), 0.0)
check_that("renders as empty", accsw.format_quota(windows).startswith("7d ░░░░░░"), accsw.format_quota(windows))

print("a payload with no rate_limit yields no windows rather than a wrong gauge")
accsw.http_get_json = lambda url, token: {"plan_type": "pro"}
check("no windows", accsw.codex_quota("token"), [])
check("formatted as a notice", accsw.format_quota([]), "— no window reported")

print("the binding window is named, so a spent model does not read as a dead account")
check("names the worst window", accsw.binding_window([("5h", 2.0, None), ("Fable", 100.0, None)]), ("Fable", 0.0))
check("unknown has no binding window", accsw.binding_window(accsw.Fail("x")), None)

print("claude is chosen on Fable first, then 5h, then the week")
prio = {"profiles": {"a": {"claude": {}}, "b": {"claude": {}}}, "active": {}}
fable = {
    ("a", "claude"): [("Fable", 60.0, None), ("5h", 90.0, None), ("7d", 10.0, None)],
    ("b", "claude"): [("Fable", 90.0, None), ("5h", 1.0, None), ("7d", 1.0, None)],
}
check("most Fable left wins despite a worse week", accsw.best_profile(prio, fable, ("claude",))[0], "a")
tied = {
    ("a", "claude"): [("Fable", 100.0, None), ("5h", 70.0, None), ("7d", 10.0, None)],
    ("b", "claude"): [("Fable", 100.0, None), ("5h", 20.0, None), ("7d", 5.0, None)],
}
check("all Fable spent falls through to the 5h window",
      accsw.best_profile(prio, tied, ("claude",))[0], "b")
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

print("headroom is free capacity in the tightest window")
check("tightest wins", accsw.headroom([("5h", 38.0, None), ("7d", 71.5, None)]), 28.5)
check("errors have no headroom", accsw.headroom(accsw.Fail("expired")), None)
check("empty has no headroom", accsw.headroom([]), None)

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
name, why = accsw.best_profile(registry, quota)
check("picks the roomier account", name, "perso")
check_that("names the binding window", why == "7d at 80% left", why)

print("best_profile honours --tool instead of mixing both")
tool_split = {
    ("perso", "claude"): [("7d", 90.0, None)],
    ("perso", "codex"): [("7d", 5.0, None)],
    ("eliza", "claude"): [("7d", 10.0, None)],
    ("eliza", "codex"): [("7d", 95.0, None)],
}
check("codex-only picks perso", accsw.best_profile(registry, tool_split, ("codex",))[0], "perso")
check("claude-only picks eliza", accsw.best_profile(registry, tool_split, ("claude",))[0], "eliza")

print("a tie keeps the account already loaded")
quota_tied = {
    ("perso", "claude"): [("7d", 50.0, None)],
    ("perso", "codex"): [("7d", 50.0, None)],
    ("eliza", "claude"): [("7d", 50.0, None)],
    ("eliza", "codex"): [("7d", 50.0, None)],
}
check("no pointless switch", accsw.best_profile(registry, quota_tied)[0], "eliza")

print("accounts with no usable data are skipped, not chosen")
quota_broken = {
    ("perso", "claude"): accsw.Fail("token expired"),
    ("perso", "codex"): accsw.Fail("token expired"),
    ("eliza", "claude"): [("7d", 99.0, None)],
    ("eliza", "codex"): [("7d", 99.0, None)],
}
check("prefers the account it could measure", accsw.best_profile(registry, quota_broken)[0], "eliza")

print("an account we could read beats one we could not, even when it is tight")
reg2 = {"profiles": {"tight": {"claude": {}}, "unknown": {"claude": {}}}, "active": {}}
tiers = {("tight", "claude"): [("Fable", 100.0, None)], ("unknown", "claude"): accsw.Fail("HTTP 429")}
check("unreadable never wins", accsw.best_profile(reg2, tiers)[0], "tight")
check("roomy still beats tight",
      accsw.best_profile(reg2, {("tight", "claude"): [("Fable", 10.0, None)],
                                ("unknown", "claude"): accsw.Fail("x")})[0], "tight")

print("an unreadable account is ranked last, not discarded")
name, why = accsw.best_profile(registry, {})
check_that("still returns a candidate", name in ("perso", "eliza"), repr(name))
check_that("says the token refreshes on use", "refreshes on first use" in why, why)
check("no captured profile for the tool returns nothing",
      accsw.best_profile({"profiles": {}, "active": {}}, {})[0], None)

print("a window past its reset has rolled over, whatever the stored number said")
check("future window keeps its number", accsw.effective_percent(38.0, now + HOUR), 38.0)
check("expired window reads as empty", accsw.effective_percent(99.0, now - 60), 0.0)
check("unknown reset keeps its number", accsw.effective_percent(38.0, None), 38.0)
check("headroom follows the rollover", accsw.headroom([("7d", 99.0, now - 60)]), 100.0)

print("format_quota reports what is LEFT, and surfaces failures verbatim")
check("error text shown", accsw.format_quota(accsw.Fail("token expired")), "— token expired")
check_that(
    "compact picker line reads as remaining",
    accsw.format_quota([("5h", 38.0, now + 2 * HOUR)]) == "5h ████░░  62% 2h",
    accsw.format_quota([("5h", 38.0, now + 2 * HOUR)]),
)

print("status lines are aligned, one per window, and colour-free when not a terminal")
lines = accsw.quota_lines([("5h", 38.0, now + 2 * HOUR), ("7d", 90.0, now + 3 * DAY)], False)
check("one line per window", len(lines), 2)
check("first line", lines[0], "5h ██████░░░░  62% left   resets in 2h")
check("second line", lines[1], "7d █░░░░░░░░░  10% left   resets in 3d")
check_that("no escape codes without a terminal", ESC not in "".join(lines) if (ESC := "\x1b") else False)
check("a just-reset window says so", accsw.quota_lines([("7d", 90.0, None)], False)[0].endswith("just reset"), True)

print("profiles name themselves after whoever is logged in")
check("simple local part", accsw.slugify("developer"), "developer")
check("dots and plus become dashes", accsw.slugify("stan.andujar+work"), "stan-andujar-work")
check("collapses runs and trims", accsw.slugify("--Stan__Test--"), "stan-test")

empty = {"profiles": {}, "active": {}}
accsw.LIVE_IDENTITY = {"claude": lambda: "developer@elizalabs.ai", "codex": lambda: None}
check("named from the local part", accsw.derive_profile(empty), ("developer", "developer@elizalabs.ai"))

print("claude wins when both are signed in, so the pair stays under one name")
accsw.LIVE_IDENTITY = {"claude": lambda: "a@work.com", "codex": lambda: "b@gmail.com"}
check("claude decides the name", accsw.derive_profile(empty)[0], "a")

print("a name already owned by a different account gets qualified, never stolen")
taken = {"profiles": {"stan": {"claude": {"email": "stan@gmail.com"}}}, "active": {}}
accsw.LIVE_IDENTITY = {"claude": lambda: "stan@outlook.com", "codex": lambda: None}
check("qualified by domain", accsw.derive_profile(taken)[0], "stan-outlook")
accsw.LIVE_IDENTITY = {"claude": lambda: "stan@gmail.com", "codex": lambda: None}
check("same account reuses its profile", accsw.derive_profile(taken)[0], "stan")

print("nothing signed in is an error, not a guess")
accsw.LIVE_IDENTITY = {"claude": lambda: None, "codex": lambda: None}
try:
    accsw.derive_profile(empty)
    check_that("raises when nothing is signed in", False, "no exception")
except accsw.Fail as error:
    check_that("raises when nothing is signed in", "nothing is logged in" in str(error), str(error))

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
