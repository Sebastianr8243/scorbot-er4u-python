# Stack 2 vendor travel bounds
Status: baseline v1 2026-11-03 (authorized by the task brief)

## Goal
Use the vendor's EncLimit and ROB_4u joint windows as Stack 2's travel bounds.
Keep optional caller caps and the existing jog, lead, step and speed limits.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| L01 | SHALL | A vendor-valid jog beyond the source reference window succeeds. | test: `tests/test_one_limit.py` |
| L02 | SHALL NOT | A jog beyond EncLimit queues motion; recovery may reduce each existing violation only. | test: `tests/test_one_limit.py` |
| L03 | SHALL | A jog or stream requires a known vendor Home zero; an unplaceable jog latches a fault. | test: `tests/test_one_limit.py` |
| L04 | SHALL | Vendor joint moves check target and setpoints against vendor windows. | test: `tests/test_one_limit.py`, `tests/test_vendor_move.py` |
| L05 | SHALL | Lab, stream, PolicyArm and example travel caps default to None; positive explicit caps have no invented ceiling. | test: `tests/test_one_limit.py`, example and stream suites |
| L06 | SHALL NOT | Source reference limits refuse or clamp a motion command, velocity jog included. | test: `tests/test_one_limit.py` (`test_velocity_jog_beyond_source_window_succeeds`, `test_velocity_jog_past_enclimit_is_still_refused`); script: `rg 'LIMITS_DEG|pose_limit|limit_window|motion_window|outside_limits|outside_window' scorbot examples scripts plugins` finds callers only inside `source_model.py` |
| L07 | SHALL NOT | This work opens USB, uses the network, changes packets or dependencies, or writes git state. | review: commands and diff |

## Out of scope
- Hardware execution or claims of measured performance.
- Changes to jog, lead, step, speed, homing search or packet rules.
- Removing source-model reference data or optional caller bounds.

## Assumptions
- The locally available PR #117 branch supplies the vendor predicate missing at this worktree's base.
- `_homed` together with `_gripper_homed` identifies vendor Home, as in PR #117.

## Open questions
None.

## Change log
- 2026-11-05: L06 now covers the velocity jog (issue #190, cut B): `manual_jog` no longer uses `source_model.motion_window` / `pose_limit_excess`.
- 2026-11-03: L01-L07 authorized by the explicit task brief; no commit requested.

## Desk evidence (2026-11-03)

- Tests were added before the fixes. The first regression run failed on the old jog bounds,
  missing vendor predicate, target/setpoint checks and fixed caps. Parser tests were corrected
  to supply the existing required session labels before checking their intended failures.
- `tests/test_one_limit.py`: 17 passed. Covers source-refused/vendor-allowed jogging,
  no queue writes past EncLimit, inward recovery, coupled recovery refusal, absolute home
  offsets, unknown home latching, legacy Home refusal, target/setpoint checks, optional
  stream caps above 180 degrees, and example arguments defaulting to None.
- Updated bench, trace, fingerprint and emergency regressions: 56 passed, 13 subtests passed.
- `vendor_limit_problem` has the identical AST to the locally available PR #117 branch.
- Packet-layer files and dependency files are unchanged. No USB, network or git writes.
- Independent external review and physical-arm checks were not run: this work is offline and desk only.

## Remaining source reference uses

The call-site audit found no external motion caller of the source limit helpers.

| Location | Remaining use | Why it stays |
|---|---|---|
| `scorbot/source_model.py:12,252` | Documentation | Explicitly describes a reference, not a motion guard |
| `scorbot/source_model.py:242` | Constant declaration | Retained source data for modelling and display |
| `scorbot/source_model.py:124` | `limit_window` | Reference window calculation; no motion caller |
| `scorbot/source_model.py:255` | `outside_limits` | Reference diagnostics; no motion caller |
| `scorbot/source_model.py:261` | `limit_excess` | Reference diagnostics; no motion caller |
| `scorbot/source_model.py:312,313` | `pose_limit_problem` | Reference diagnostic text; no motion caller |
| `tests/test_source_model.py:214,215,216,217,218,226,236` | Reference-value and modelling assertions | Tests reference data, never sends motion |
| `scorbot/README.md:24`, `docs/protocol/SOURCE_AUDIT.md:200` | API/source documentation | Describes retained reference data |
| This requirements file | Audit requirement and report | Documentation only |

Existing motion and session tests now use vendor Home where motion needs it; legacy and inch
Home tests remain and assert refusal of post-home jogging. Source-window tests keep checking
the reference model. Two old cap tests were renamed and rewritten for the None default because
their 180-degree assertions are obsolete; no test was deleted. Explicit-cap tests remain.
