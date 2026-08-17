#!/usr/bin/env python3
"""Round-trip the real macOS keychain with a realistic payload — never a real secret.

This is the test that was missing. The unit tests only ever moved tiny strings, so a
write mechanism that wrapped long lines passed everything and then printed an 8 KB
credential to the terminal the first time it met a real blob. Size is the point here.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import os
import sys
from pathlib import Path

os.environ["ACCSW_OFFLINE"] = "1"  # no test may reach the network

ACCSW = Path(__file__).resolve().parent.parent / "accsw"
loader = importlib.machinery.SourceFileLoader("accsw", str(ACCSW))
accsw = importlib.util.module_from_spec(importlib.util.spec_from_loader("accsw", loader))
loader.exec_module(accsw)

SERVICE = "accsw-selftest-delete-me"

# Same shape and order of magnitude as a real credential blob, with nothing real in it.
FAKE = json.dumps(
    {
        "claudeAiOauth": {
            "accessToken": "fake-access-" + "A" * 180,
            "refreshToken": "fake-refresh-" + "R" * 180,
            "expiresAt": 1787018872199,
            "scopes": ["user:inference", "user:profile"],
            "subscriptionType": "max",
        },
        "organizationName": "Sociéte Générale ✨ — accented, to force hex encoding",
        "padding": {f"server-{index}": "x" * 120 for index in range(60)},
    }
)

results: list[bool] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append(condition)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + ("" if condition else f"  {detail}"))


def cleanup() -> None:
    subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", accsw.CLAUDE_KEYCHAIN_ACCOUNT],
        capture_output=True,
    )


try:
    cleanup()

    print(f"a realistic blob survives the keychain ({len(FAKE)} bytes, non-ASCII included)")
    check("absent item reads as None", accsw.keychain_read(SERVICE) is None)

    accsw.keychain_write(SERVICE, FAKE, "accsw selftest")
    back = accsw.keychain_read(SERVICE)
    check("round-trips byte for byte", back == FAKE, f"got {len(back or '')} of {len(FAKE)}")
    check("still parses as JSON", json.loads(back)["claudeAiOauth"]["subscriptionType"] == "max")

    print("overwriting an existing item replaces it")
    accsw.keychain_write(SERVICE, FAKE.replace("max", "pro"), "accsw selftest")
    check("second write wins", json.loads(accsw.keychain_read(SERVICE))["claudeAiOauth"]["subscriptionType"] == "pro")

    print("delete is verified, and repeats are harmless")
    accsw.keychain_delete(SERVICE)
    check("gone after delete", accsw.keychain_read(SERVICE) is None)
    accsw.keychain_delete(SERVICE)
    check("deleting twice does not raise", True)

    print("a diagnostic that echoes the credential is withheld")
    echoed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr=f'unknown command "{FAKE.encode().hex()}"'
    )
    message = accsw.diagnose(echoed, FAKE)
    check("hex payload never reaches the message", FAKE.encode().hex() not in message, message[:80])
    check("says why it was withheld", "withheld" in message, message)
    clean = subprocess.CompletedProcess(args=[], returncode=51, stdout="", stderr="auth failure")
    check("ordinary stderr still surfaces", accsw.diagnose(clean) == "auth failure")
finally:
    cleanup()

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
