# Design: adopting a home without a search (`adopt_home`)

**PARKED 2026-10-06 (owner: over-engineered).** Not needed to start: home in SCORBASE, close it, then run the existing legacy `home()` from that pose. The arm rests on its switches after a SCORBASE home (`docs/manual/HARDWARE_REFERENCE.md`, line 94), and `home()` skips any axis whose switch is already on, so it should move nothing and only record the counts. The counter question below is also answered by existing data: the 2026-09-29 idle capture, taken after power-on and before homing, has every joint at about zero (`docs/protocol/VENDOR_DLL_PROTOCOL.md`, line 159), so near-zero counts say nothing about the pose. Revive this only if that plan fails.

Written 2026-10-06. **Design only; nothing implemented, nothing run on the arm.** For the owner to approve: it adds a home path that skips the legacy search and `start_position_confirmed`, which is a change to the owner's homing rule (root `CLAUDE.md`, the `home()` row).

## Problem

The legacy `home()` (order 18) searches the switches and only works from its fixed start pose. On 2026-10-06 it failed after about 4 s (`base-first-02`: `Legacy controller returned error code 1`, a joint error word at or above 40), so no jog could be tested. Homing with SCORBASE works. The owner wants Python to proceed from a home that SCORBASE (or the operator) already established, instead of searching.

## What "home" means in this code

`Scorbot.home()` does two things after order 18 returns: `_home_counts = counts.copy()` (the raw encoder counts at that moment) and `_homed = True` (`robot.py:702-703`). Every angle, jog gate and stream bound is computed from `signed_count_delta(count, _home_counts)`. So home is **bookkeeping about where the arm is**, not a controller state. The legacy worker keeps its own host-side `home` flag, but it only gates order 19 (`moveXYZ`), which `Scorbot` never sends (`docs/protocol/PROTOCOL.md`, line 282). Jogs (orders 4 to 13) and streams (21) do not need it.

Adopting a home can therefore send **nothing**: no order 18, and above all no vendor set-position command `48`. That byte is one the legacy code never sends, so it needs a USB capture first (root `CLAUDE.md`, protocol rule).

## The question to answer first (no Python-commanded motion)

Adopting is only meaningful if the counters tell us something about the pose. They do only if the controller's counters are **not** zeroed by power-on or by a USB connect. Nothing we have settles that. The logs of 2026-10-06 show counters near zero (`base 65535, shoulder 2, elbow 65535, wrist -2, 4`), which is equally consistent with "zeroed at power-on wherever the arm was" and with "SCORBASE hard home zeroed them" (the manual says SCORBASE zeroes all encoders at hard home, p. 23). Also, `base-first-02` drove the shoulder search for about 4 s after those readings, so the current counts are unknown.

**Counter persistence test, at the lab, idle recordings only (`record_raw_state.py`):**

1. Home with SCORBASE, close it, take a Python idle recording. Expect counts near 0.
2. Move one joint a few degrees away with SCORBASE, close it, take another idle recording. If the counts show that offset, counters persist across SCORBASE and a Python connect. If they read 0, the connect reset them.
3. Power-cycle the controller with the arm left away from home, take another idle recording. If counts read 0, power-on zeroes them: near-zero counts then say nothing about the pose.

Outcome decides the design:

| Result | Consequence |
|---|---|
| Counters persist (2 offset, 3 zero-on-power-cycle only) | Near-zero counts plus "SCORBASE homed this power cycle" is real evidence. Build `adopt_home` as below |
| Connect resets counters (2 reads 0) | Adoption cannot work after a SCORBASE home. Drop this design; use a USBPcap capture of SCORBASE homing and write a Python search from it |
| Counters do not persist across SCORBASE (re-zeroed on its exit) | Same, adoption is unsafe |

## Design (if the counters persist)

`Scorbot.adopt_home(*, scorbase_homed_this_power_cycle: bool = False, note: str)`

**Sends nothing.** Bookkeeping only; reads fresh state and sets `_home_counts` and `_homed`, exactly as `home()` does afterwards.

**Refuses (raises, latches nothing, queues nothing) unless all of these hold:**

1. connected, session not faulted, not streaming; motors state not required (nothing moves), but jogs still need `enable()`;
2. `scorbot_homed_this_power_cycle` is `True` and `note` is non-empty: the operator states that SCORBASE homed the arm in this power cycle and nothing has moved it since (typed word in the scripts);
3. `home_switch_bits` below 32, as `home()` requires (`libdef.get_switch` misreads higher);
4. the arm is at rest: several fresh samples (`packet_index` increasing) with every arm and wrist motor within `STABLE_COUNTS` of each other;
5. the five arm and wrist motors read within `ADOPT_ZERO_BAND` counts of zero (proposal: `DRIFT_COUNTS`, 20), taken with `signed_count_delta`, never plain subtraction. The gripper is left out;
6. if a calibration is loaded, `validate_home` passes, as `home()` does.

The count check is only a **consistency** check (the arm is where the counter was last zeroed, per the test above). It does not prove the pose. The tape-measure check does.

**Physical check, independent of the counters (recommended, prompted, logged, not machine-verifiable):** `docs/lab/ACCEPTANCE_RUN.md` section 1: at zero counts the tool point is about 169 mm out from the base axis and 504 mm above the base bottom (shoulder axis 349 mm, unmeasured; manuals say 346 to 364). The scripts ask the operator to confirm it before the first move. The shoulder has only about 422 counts (3.7 degrees) of up-margin, so a pose a few degrees off eats it.

**Logging, so it is never mistaken for a searched home:**
- event `home_adopted` (not `home_complete`), carrying the state, the note, and `home_source: "adopted"`; a searched home logs `home_source: "searched"`;
- `scripts/review_lab_logs.py` and `scripts/watch_lab_log.py:ALARM_EVENTS` parse event names: both change in the same commit, and the review prints "HOME ADOPTED, NOT SEARCHED" prominently;
- session metadata gets `home_source`; comparisons and exports refuse to pool adopted and searched sessions silently (same idea as the real/simulated rule).

**Entry points:** a separate `--adopt-home` flag on `examples/bench_joint.py`, `bench_stream.py` and `python -m scorbot.lab`, where the `HOME` search step is replaced by typing `ADOPT`. The default stays the legacy search. No prompt is shared between the two.

**Why this may be closer to the source model than the legacy search:** the model's zero is the vendor's home pose, where SCORBASE zeroes the counters. The legacy search runs 12 messages past each switch and does not back off by the vendor's per-axis offsets (shoulder -190, elbow +45 counts), so its session home differs from the model's by a small amount per joint (`scorbot/source_model.py` header; `docs/manual/HARDWARE_REFERENCE.md` line 73). An adopted SCORBASE home removes that offset. Unverified.

## What does not change

`openScorbot/` is untouched (no packet, sequence byte or sleep change). The 5 degree jog ceiling, the stream lead limit and the whole-pose joint-limit check apply to an adopted home exactly as to a searched one. `disable()` and faults still clear home. Wrist jogs stay disabled.

## Tests (simulator only; no USB)

- adopts and then jogs: counts near zero, at rest, confirmed: `jog_joint` works, `home_adopted` logged with `home_source`;
- refuses, queues nothing and latches nothing: not confirmed, empty note, counts outside the band (each of the five motors), arm still moving, switch bits at or above 32, faulted session, streaming, calibration `validate_home` failure, not connected;
- `signed_count_delta` seam: a motor reading 65535 counts as zero, not far from it;
- `disable()` and a fault clear an adopted home;
- an adopted session is flagged by `review_lab_logs.py` and `watch_lab_log.py`, and a mixed adopted/searched pool is flagged;
- the simulator test that proves no USB opens (`test_import_loads_no_usb...`) still passes; add a test that `adopt_home` sends no command (`robot.sim.commands` unchanged).

## Root `CLAUDE.md` change this needs (the owner's rule)

Replace the `home()` row's "needs `start_position_confirmed=True`" with two paths: `home()` as today, and `adopt_home()` as above, each with its enforcing test. Add the capture to the next-visit list.

## Next-visit additions

- the counter persistence test above (idle recordings only);
- a USBPcap capture of SCORBASE homing (`docs/lab/USB_CAPTURE.md`): the evidence needed for a real Python homing search later, and it touches no legacy bytes now.

## Open questions for the owner

1. Is the counter persistence test acceptable as the first thing to do at the lab, before any code?
2. Should adoption require the typed word every time, or may a profile remember "SCORBASE homed" for the session?
3. Band for "near zero": 20 counts (`DRIFT_COUNTS`), or tighter (idle samples were within 4)?
4. Do you want `adopt_home` in the guided session (`scorbot.lab`) as well as the bench scripts?
