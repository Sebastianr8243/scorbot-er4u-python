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
- **I (first reading, superseded below)** The shoulder drove into a hard stop before the switch.
- **N** Encoder counts during the 4.2 s and at the moment of failure. The failure path logs no state, so we cannot tell how far or which way the shoulder moved, or whether the switch bit ever flickered.

## Other things the logs do pin down

- **Command latency (V):** disable (16) 657, 666 and 1020 ms at connect; enable (17) 296 ms; exit (528) 906 and 985 ms. A real `HOME` or jog is longer, so command timeouts must not be set near these.
- **Packet rate (V, idle sync loop, not a jog):** median 54 packets/s (range 36 to 60) from `packet_index` against the monotonic clock, so **18.5 ms per message** against the 13 ms of `WRITE` + `READ` sleeps. That is about 5.5 ms of overhead per message (readData parsing, the hex round trip, USB), the number the optimization plan wanted. It is one idle capture; a jog may differ.
- **Power-on counts (V):** every motor within +-12 counts of 0; the base sat at 65535 in two runs and 0 in the third. The 0/65535 seam is real at rest, so `signed_count_delta` is needed on real data, not only in tests.
- **Rest stability (V):** over 10 s idle, encoder counts and error counts did not change at all (no jitter, no drift). The simulator's rest-jitter model is therefore a guess, not an observation.
- **Following-error counts (V):** nonzero while disabled (shoulder 11, 3, 1 across runs) and zero right after enable. Read as "position error of an unpowered, slightly displaced motor"; **I**, no manual statement.
- **Clock (V):** `host_monotonic_ns` is quantized (sample spacing 500 ms, with 516 ms steps), so sub-15 ms timing from these logs is not reliable.

## Revised reading, after the ULPGC thesis (2026-10-08)

New facts (`docs/protocol/ULPGC_THESIS_FINDINGS.md`): a pressed switch sets its bit and our bit map is right, so "the switch was never seen because the polarity is inverted" is mostly out; the controller also has its own collision flag at IN offset 59, which we never decode.

What the search was doing, from the code (V) and the measured loop rate:

- `setHome.homing` raises the shoulder setpoint by `h_vel` = 10 counts per message (a 12-message ramp, then steady) with a 20 ms write + read sleep per message, so about 25 ms per message with the 5.5 ms overhead measured above: **about 390 counts/s, 3.5 degrees/s** on the shoulder scale (113.5 counts/degree).
- `Legacy controller returned error code 1` is the **host's own** check `getError >= MAX_ERROR`, 40 counts, which is 0.35 degrees of lag (V, `setHome.py`). It is not the controller's collision flag, which the legacy code never reads.

| Reading | What it predicts | Against the logs |
|---|---|---|
| **A. Start within 3.7 degrees of the upper stop, search runs into it (our first reading)** | 49 messages to cover 3.7 degrees, plus about 4 to trip `MAX_ERROR`: **about 1.3 s** | Observed **4.2 s**. Does not fit. |
| **B. Shoulder tracks for the whole 4.2 s, then stalls** | about 160 messages, **13.5 degrees** of travel (17 to 19 at the faster loop rate) | The operator saw "a few degrees". Does not fit unless the start was about 10 degrees below the stop, and then the switch (which is within a few degrees of home) lies on the way and would have ended the search. |
| **C. The shoulder lags the commanded ramp, the lag grows to 40 counts at about 4 s** | a slow rise of a few degrees, motor straining, then the abort | **Fits the duration, the "a few degrees" and the "noise".** |
| **D. Coupling: the held elbow and wrist motors fight the shoulder** | same signature as C | Cannot be separated from C with these logs. |

My best reading (I): **C or D, the shoulder could not follow the legacy search speed in that pose**, and the 40-count host limit is tight. Consistent with this, the vendor does not stream a ramp for homing; it uses a controller-side drive in a homing mode (`VENDOR_HOMING_TRACE.md`), where the host never watches a following error. SCORBASE jogs the waist at 750 to 7500 counts/s (thesis p. 52), so the controller can servo far faster than our search; whether the shoulder can at this pose is open.

## What to capture at the next visit

1. Log counts, both position-error words, switch bits and the collision byte (IN offset 59) every message during any legacy home search, not only at the end. This separates B, C and D at once.
2. A no-motion switch check with the arm idle, pressing each microswitch with a screwdriver (thesis pp. 54 to 56). It confirms bit-to-axis for this arm, including pitch vs roll.
3. A 1 degree shoulder jog on its own, from a pose near home, with the same logging: does the shoulder track the ramp, and how big does the error get?
4. Do not repeat the legacy `HOME` from the 10-06 pose without that logging, and not from a folded-down pose.
