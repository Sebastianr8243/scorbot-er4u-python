# Offline jog safety changes

This note accompanies the exact-count jog planner, signed response decoder, wrist jog hold, and dry-run preview on draft PR #1. It was prepared with a documentation subagent and checked against the implementation. All count-to-angle scales are inherited software assumptions. They are **not measurements of this arm**.

## What changed

- The legacy USB jog path uses `openScorbot/motion_profile.py` to choose integer setpoint increments. Their sum equals the requested integer motor-count target at speeds 1–20. A short request cannot accidentally accumulate a larger count target because of the old ramp.
- The Python state decoder keeps the raw unsigned counts and now also records each encoder sign byte and a decoded signed value. It rejects sign bytes other than 127 or 128. The 65535 wrap rule is inferred from the existing protocol code and still needs comparison with real controller packets.
- The supervised Python adapter rejects live `wrist_pitch` and `wrist_roll` jogs pending two-motor bench verification. **The old GUI is a separate legacy path and is not covered by this API gate.**
- `Scorbot().preview_jog(...)` and `examples/preview_jog.py` show the exact planned increment sequence and per-motor count deltas without opening USB. If saved signed counts are supplied, they show target signed counts. The bench script prints and logs that preview before it offers the MOVE prompt.

The preview shows **requested controller setpoints**, not observed motion, physical angle, clearance, or stop performance. It does not authorize a live move.

## Use the preview offline

From the repository root:

```powershell
.\.venv\Scripts\python.exe .\examples\preview_jog.py --joint base --delta 1 --speed 10
```

The result includes `counts_per_motor`, `increments`, and `motor_count_deltas`. If you have a saved JSON state with `signed_encoder_counts`, add `--state-json .\saved-state.json` to show signed count targets. A saved state may be stale; the script deliberately makes no freshness claim. The wrist options produce a preview but remain disabled for live jogs through `Scorbot.jog_joint`.

## Offline checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests exercise short and long profiles at several speeds, exact final counts, wrist motor direction mapping, invalid sign bytes, negative count decoding, and the no-command preview. They use synthetic packets and do not connect to the controller. A passing test establishes software behavior only.

## Next lab session

1. Record the arm and controller IDs, software commit, USB driver, operator, physical stop arrangement, and confirmed homing start pose. Clear the arm's travel path.
2. Capture idle raw packets first with `examples/record_raw_state.py`. Review packet freshness, raw counts, sign bytes, and switch bits.
3. Run `examples/bench_joint.py` for one base direction only. It displays the exact preview before the MOVE prompt. Compare its count prediction with the fresh before/after log and note observed physical direction.
4. Repeat a small base jog in the opposite direction, then evaluate shoulder and elbow separately. Stop at the first unexpected motion, sign mismatch, stale feedback, timeout, or uncertain motor state.
5. Measure physical joint angles with an independent reference before using absolute angles or declaring software limits. Keep wrist jogs gated until both wrist motors and physical pitch/roll are measured.

The queued Python `disable()` command is not an emergency stop. Use the controller's physical stop if motion or motor state is uncertain. Do not copy values from manuals, vendor displays, or the inherited software scales into a physical calibration file.

## Open questions

The actual sign convention, wrap behavior, movement direction, home repeatability, count-to-angle scale, backlash, safe limits, and stop behavior on this specific arm remain unverified. Keep the raw logs and independent measurements for those checks.
