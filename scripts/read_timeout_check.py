"""Lab step T: how long one read on Intelitek's driver takes when nothing was written first. No motion.

    python scripts/read_timeout_check.py --online [--timeout-ms 1500]

Opens the driver link exactly as ``device_file`` does (exclusive, so close SCORBASE and the vendor tools),
writes nothing, does one read, prints how long it took and how it ended, and closes. The controller answers
messages; with no message it should not answer, so the read shows the driver's own timeout. Read from
``ERUSB.sys`` (``docs/protocol/LEDGER.md`` PL30, PL31): it waits about 5 s and then returns no bytes, which
``device_file`` raises as ``TimeoutError``. Pass: a ``TimeoutError`` that ends by itself in 4 to 6 s
(``docs/lab/LAB_CHECKLIST.md``), exit 0. One other outcome: a link error from empty reads in under 1 s
(the lab PC, 2026-11-12) prints FINDING and exits 4: the driver did not wait, the link is not proven dead,
run the probe. Any other ending fails (exit 1), however long it took.

Sends no packet, so it commands nothing. Refuses to run without ``--online``.
"""

import argparse
import sys
import time

from scorbot.transport import device_file


def check(link, timeout_ms):
    """One read with nothing written. Returns ``(seconds, kind, detail)``; the caller owns the link."""
    started = time.monotonic()
    try:
        link.endpoint_in.read(bytearray(64), timeout_ms)
        kind, detail = "answered", "64 bytes, unexpected: nothing was sent"
    except TimeoutError as error:
        kind, detail = "timeout", str(error)
    except device_file.DeviceLinkError as error:
        kind, detail = "link error", str(error)
    return time.monotonic() - started, kind, detail


FINDING_EXIT = 4       # not 0 (pass), 1 (fail), 2 (refused); lab_day.py already uses 3 for a skip
FINDING = "driver returned no bytes at once; the link is not proven dead, run the probe"
AT_ONCE_S = 1.0


def passed(seconds, kind):
    return kind == "timeout" and 4.0 <= seconds <= 6.0


def outcome(seconds, kind, detail):
    """PASS, FAIL, or FINDING: the empty-reads link error in under a second that the lab PC gave on 2026-11-12.

    ``device_file`` gives up after ``MAX_EMPTY_READS`` empty reads whatever the timeout, so a driver that
    returns empty reads at once never shows its own timeout. That says nothing about the link (the probe
    afterwards read 200 of 200), so it is not a FAIL; it is not a pass either.
    """
    if passed(seconds, kind):
        return "PASS"
    if kind == "link error" and seconds < AT_ONCE_S and "empty reads in a row" in detail:
        return "FINDING"
    return "FAIL"


def main(argv=None, *, api=None):
    parser = argparse.ArgumentParser(description="Lab step T: one read, no write, timed. Never moves the arm.")
    parser.add_argument("--online", action="store_true", help="really open the controller's driver link")
    parser.add_argument("--timeout-ms", type=int, default=1500, help="the caller's timeout (default: the legacy TIME_OUT_R)")
    parser.add_argument("--path", default=device_file.DEFAULT_PATH)
    args = parser.parse_args(argv)
    if not args.online:
        print("refusing: pass --online to open the real driver link")
        return 2
    link = device_file.DeviceLink(args.path, api=api)
    try:
        seconds, kind, detail = check(link, args.timeout_ms)
    finally:
        link.close()
    verdict = outcome(seconds, kind, detail)
    note = f"; {FINDING}" if verdict == "FINDING" else ""
    print(f"{verdict} read took {seconds:.2f} s, ended as {kind}: {detail}{note}")
    return {"PASS": 0, "FINDING": FINDING_EXIT}.get(verdict, 1)


if __name__ == "__main__":
    sys.exit(main())
