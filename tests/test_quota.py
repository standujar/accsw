#!/usr/bin/env python3
"""Unit tests for the quota layer: window parsing, formatting, auto-selection.

No network: the HTTP layer is replaced with canned payloads shaped like the real
responses (Claude /api/oauth/usage, Codex wham/profiles/me).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import time
from pathlib import Path

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

print("claude payloads map to windows")
accsw.http_get_json = lambda url, token: {
    "five_hour": {"utilization": 38.0, "resets_at": now + 2 * HOUR},
    "seven_day": {"utilization": 71.5, "resets_at": now + 3 * DAY},
    "seven_day_opus": {"utilization": 90.0, "resets_at": now + 3 * DAY},
}
windows = accsw.claude_quota("token")
check("two windows kept", [label for label, _, _ in windows], ["5h", "7d"])
check("percent read", [percent for _, percent, _ in windows], [38.0, 71.5])
check_that("reset carried", all(ts is not None for _, _, ts in windows))

print("claude 'percent' spelling is accepted too")
accsw.http_get_json = lambda url, token: {"five_hour": {"percent": 12.0, "resets_at": now + 600}}
check("percent fallback", accsw.claude_quota("token")[0][1], 12.0)

print("codex payloads map to windows")
accsw.http_get_json = lambda url, token: {
    "rateLimits": {
        "primary": {"usedPercent": 12.0, "windowDurationMins": 300, "resetsAt": now + 45 * 60},
        "secondary": {"usedPercent": 9.0, "windowDurationMins": 10080, "resetsAt": now + 5 * DAY},
    }
}
windows = accsw.codex_quota("token")
check("labels from window duration", [label for label, _, _ in windows], ["5h", "7d"])
check("used percent read", [percent for _, percent, _ in windows], [12.0, 9.0])

print("missing rate limits degrade to an empty window list")
accsw.http_get_json = lambda url, token: {}
check("no windows", accsw.codex_quota("token"), [])
check("formatted as a notice", accsw.format_quota([]), "— no window reported")

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
check_that("explains itself", "left in its tightest window" in why, why)

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
check("falls back to the only known one", accsw.best_profile(registry, quota_broken)[0], "eliza")
check("all unknown returns nothing", accsw.best_profile(registry, {})[0], None)

print("a window past its reset has rolled over, whatever the stored number said")
check("future window keeps its number", accsw.effective_percent(38.0, now + HOUR), 38.0)
check("expired window reads as empty", accsw.effective_percent(99.0, now - 60), 0.0)
check("unknown reset keeps its number", accsw.effective_percent(38.0, None), 38.0)
check("headroom follows the rollover", accsw.headroom([("7d", 99.0, now - 60)]), 100.0)

print("format_quota reports what is LEFT, and surfaces failures verbatim")
check("error text shown", accsw.format_quota(accsw.Fail("token expired")), "— token expired")
check_that(
    "window line reads as remaining",
    accsw.format_quota([("5h", 38.0, now + 2 * HOUR)]) == "5h ██████░░░░  62% left resets 2h",
    accsw.format_quota([("5h", 38.0, now + 2 * HOUR)]),
)

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
