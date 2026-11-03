# PolicyArm vendor travel requirements
Status: baseline v1 2026-11-03 (authorized task brief)

## Goal
Bound PolicyArm and the LeRobot plugin by Intelitek's joint-angle window and encoder box after this session's vendor Home. Physical zero and signs remain unverified until lab G/X.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| VL01 | SHALL | Allow travel beyond 2 degrees inside both vendor windows, including vendor base +170 degrees and roll above 360 degrees. | test: `tests_extra/test_policy_vendor_limits.py` |
| VL02 | SHALL | Hold a frame outside either vendor window, including coupled elbow/pitch angles and session-home offsets. | test: `tests_extra/test_policy_vendor_limits.py` |
| VL03 | SHALL NOT | Accept act before a successful vendor Home in this connection. | test: `tests_extra/test_policy_arm_vendor_home.py` and `tests_extra/test_policy_vendor_limits.py` |
| VL04 | SHALL NOT | Change step, lead, speed, packets, fault/stop paths or the jog ceiling. | script: requested full verify suite; review: diff |
| VL05 | SHALL NOT | Verification access USB, move hardware, use network, stage, commit or push. | review: commands use tests and simulator only |

## Out of scope
- Physical validation, other SDK jog/stream callers and their existing limits.
- Changing policy degree conventions or the vendor count conversion.

## Assumptions
- Existing policy degrees retain their preview_jog signs; vendor angles are computed from absolute counts with vendor_model.
- Vendor Home establishes nominal encoder zero; physical correspondence is unverified.

## Open questions
- None for simulator implementation; lab G/X must confirm physical zero and signs.

## Change log
- 2026-11-03 Baseline from the authorized task brief; full-frame hold chosen for a vendor-bound refusal.

## Verification (2026-11-03, simulator only)
| ID | Result | Evidence |
|---|---|---|
| VL01 | pass | Vendor travel tests: 11 passed, including vendor base +170 and roll beyond 360. |
| VL02 | pass | Same tests cover joint and encoder refusal, coupled angles and absolute session-home offsets. |
| VL03 | pass | Full suite includes vendor Home packet/confirmation checks and disconnected/invalidated Home refusal. |
| VL04 | pass | Ruff: all checks passed; compileall: exit 0; full pytest: 1772 passed, 6 skipped, 12892 subtests passed in 332.16 s; docs: `docs ok`. Diff leaves step/lead constants, speed calculations, packet code and jog ceiling unchanged. |
| VL05 | pass | Only test/simulator commands run; no USB access, physical motion, external network, staging, commit or push. LeRobot check used this worktree and offline cache under `.tmp`: all six checks passed, 60 frames, replay difference at most 0.212 degrees. |

One full-suite warning remains: `test_home_stop` leaves a mocked legacy worker comparing a MagicMock with an integer. LeRobot warns that TorchCodec cannot load and uses its existing PyAV fallback. Initial pytest temp and Hugging Face cache permission failures were resolved with local `.tmp` paths; the intentional stream annotation change required updating its API snapshot. Physical zero and signs remain unverified until lab G/X.
