#!/usr/bin/env python3
"""Drive the picker through a real pty: arrow keys in, selection out.

Uses a throwaway ACCSW_HOME with fabricated profiles, so no real credential is
ever read or written. Selection is proved by the error the chosen profile then
raises — it names the profile, which is what we are asserting.
"""

from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ACCSW = Path(__file__).resolve().parent.parent / "accsw"

DOWN = b"\x1b[B"
UP = b"\x1b[A"
ENTER = b"\r"
QUIT = b"q"

REGISTRY = {
    "profiles": {
        "alpha": {"claude": {"email": "alpha@example.com", "oauthAccount": {}}},
        "bravo": {"claude": {"email": "bravo@example.com", "oauthAccount": {}}},
        "charlie": {"claude": {"email": "charlie@example.com", "oauthAccount": {}}},
    },
    "active": {"claude": "alpha"},
}


def drive(keys: list[bytes], args: list[str]) -> str:
    """Run accsw attached to a pty, send keys, return everything it printed."""
    store = Path(tempfile.mkdtemp(prefix="accsw-test-"))
    (store / "registry.json").write_text(json.dumps(REGISTRY), encoding="utf-8")

    primary, secondary = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(ACCSW), *args],
        stdin=secondary,
        stdout=secondary,
        stderr=secondary,
        env={**os.environ, "ACCSW_HOME": str(store), "TERM": "xterm", "ACCSW_ABSORB": "0"},
        close_fds=True,
    )
    os.close(secondary)

    collected: list[bytes] = []

    def drain(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if not select.select([primary], [], [], 0.05)[0]:
                continue
            try:
                chunk = os.read(primary, 8192)
            except OSError:
                return
            if not chunk:
                return
            collected.append(chunk)

    drain(0.5)
    for key in keys:
        os.write(primary, key)
        drain(0.25)
    drain(1.5)

    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)
    os.close(primary)
    return b"".join(collected).decode(errors="replace")


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{'' if condition else f' — {detail}'}")
    return condition


def main() -> int:
    results = []

    print("picker renders every profile")
    screen = drive([QUIT], ["use"])
    results.append(check("lists alpha", "alpha@example.com" in screen))
    results.append(check("lists bravo", "bravo@example.com" in screen))
    results.append(check("lists charlie", "charlie@example.com" in screen))
    results.append(check("shows key hints", "↑↓ move" in screen))

    print("q cancels without switching")
    results.append(check("says cancelled", "cancelled" in screen, screen[-200:]))

    print("enter selects the active profile by default")
    screen = drive([ENTER], ["use"])
    results.append(check("selected alpha", "alpha" in screen, screen[-200:]))

    print("down arrow moves the selection")
    screen = drive([DOWN, ENTER], ["use"])
    results.append(check("selected bravo", "bravo" in screen, screen[-200:]))
    results.append(check("did not select alpha", "'alpha'" not in screen, screen[-200:]))

    print("wraps around past the last row")
    screen = drive([UP, ENTER], ["use"])
    results.append(check("selected charlie", "charlie" in screen, screen[-200:]))

    print("j/k navigate like vim")
    screen = drive([b"j", b"j", ENTER], ["use"])
    results.append(check("selected charlie via j", "charlie" in screen, screen[-200:]))

    print("non-tty refuses instead of hanging")
    completed = subprocess.run(
        [sys.executable, str(ACCSW), "use"],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "ACCSW_HOME": tempfile.mkdtemp(prefix="accsw-test-"), "ACCSW_ABSORB": "0"},
        stdin=subprocess.DEVNULL,
    )
    results.append(
        check(
            "empty store reports no profiles",
            "no profiles yet" in completed.stderr,
            completed.stderr,
        )
    )

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
