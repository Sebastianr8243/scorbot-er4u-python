# 2026-10-06 lab logs: what they show (desk reading, 2026-10-08)

Three runs, real arm, `data_source: real`, `motion_source_sha256 5ef58dd6...` in all three. Labels: **V** = read straight from the logs or the legacy code; **I** = inferred; **N** = not in the logs.

| Run | What happened |
|---|---|
| `idle-01` | Connect, LEDs as expected, 20 samples over 10 s, exit. |
| `base-first-01` | Connect, LEDs as expected, operator declined before homing, exit. No motion. |
| `base-first-02` | Connect, enable, LEDs as expected (motors lit), `HOME` (order 18) failed after 4.2 s with `Legacy controller returned error code 1`, motors switched off. No jog was sent. |

## The home failure

- **V** The failing command is order 18 on a start state of all switch bits 0, motors enabled, all following-error counts 0. It ran 4.22 s (monotonic) and returned 1.
- **V** In `setHome.homing` the shoulder is searched first. Result 1 with the text "Joint limit reached" is the branch `getError(...) >= MAX_ERROR` (`MAX_ERROR` is 40 in `conf.py`): the shoulder's following error passed 40 before its switch was read as pressed. Nothing else in that function returns 1 this early.
- **V** The operator saw the shoulder rise a few degrees, stop, and make noise (`docs/lab/AGENT_RUNBOOK.md`). That matches the line above.
- **I** The shoulder drove into a hard stop before the switch. The shoulder search moves the counts in the + direction, and `CLAUDE.md` puts under 4 degrees of travel above home, so a start pose at or above the switch would end exactly like this. This is the reason `home_inch` searches both ways.
- **N** Encoder counts during the 4.2 s and at the moment of failure. The failure path logs no state, so we cannot tell how far or which way the shoulder moved, or whether the switch bit ever flickered.

## Other things the logs do pin down

- **Command latency (V):** disable (16) 657, 666 and 1020 ms at connect; enable (17) 296 ms; exit (528) 906 and 985 ms. A real `HOME` or jog is longer, so command timeouts must not be set near these.
- **Packet rate (V, idle sync loop, not a jog):** median 54 packets/s (range 36 to 60) from `packet_index` against the monotonic clock, so **18.5 ms per message** against the 13 ms of `WRITE` + `READ` sleeps. That is about 5.5 ms of overhead per message (readData parsing, the hex round trip, USB), the number the optimization plan wanted. It is one idle capture; a jog may differ.
- **Power-on counts (V):** every motor within +-12 counts of 0; the base sat at 65535 in two runs and 0 in the third. The 0/65535 seam is real at rest, so `signed_count_delta` is needed on real data, not only in tests.
- **Rest stability (V):** over 10 s idle, encoder counts and error counts did not change at all (no jitter, no drift). The simulator's rest-jitter model is therefore a guess, not an observation.
- **Following-error counts (V):** nonzero while disabled (shoulder 11, 3, 1 across runs) and zero right after enable. Read as "position error of an unpowered, slightly displaced motor"; **I**, no manual statement.
- **Clock (V):** `host_monotonic_ns` is quantized (sample spacing 500 ms, with 516 ms steps), so sub-15 ms timing from these logs is not reliable.

## What to capture at the next visit

1. Log the encoder counts and switch bits when a home fails, and a few samples during the first seconds of the search. Without them a repeat failure teaches nothing.
2. A USB trace of the first 4 s of a legacy `HOME` from the same pose (`docs/lab/USB_CAPTURE.md`), to see which way the shoulder is driven.
3. Do not repeat the legacy `HOME` from this pose. The shoulder hitting its stop is the likely cause, and repeating it drives the stop again.
