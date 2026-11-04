# Legacy DLL fault and packet parity requirements
Status: baseline v1 2026-11-03 (authorized task brief)

## Goal
Match the recorded vendor stop, pitch scale and unused setpoint slots in the legacy jog path.
Physical-arm behaviour remains unverified; all checks are desk-only.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| LF01 | SHALL | A ramp position error whose signed absolute value is above `ImpactDetect` (70; LEDGER.md PL98) ends all four jog functions with 47 followed by holds at the last reply's counts, reporting only result 1. | test: `tests/test_software_stop.py::LegacyStopTests::test_joint_error_without_a_stop_clears_and_holds` |
| LF02 | SHALL | Every settle reply refreshes the position error; an error above `ImpactDetect` immediately sends the same fault stop. | test: `tests/test_software_stop.py::LegacyStopTests::test_joint_error_during_settle_clears_and_holds` |
| LF03 | SHALL | Any nonzero IN byte 59 during ramp or settle sends the same fault stop and only result 1. | test: `tests/test_software_stop.py::LegacyStopTests::test_impact_during_ramp_or_settle_clears_and_holds` |
| LF04 | SHALL | Pitch uses 2511/90 counts per degree in planning and both legacy conversion directions; one degree rounds to 28 counts. | test: `tests/test_motion_profile.py`; `tests/test_software_stop.py::LegacyStopTests::test_pitch_conversion_matches_the_vendor_scale` |
| LF05 | SHALL | Home and post-Home gripper OUT bytes 36-43 equal the PL51 recording's zero bytes. | test: `tests/test_vendor_gripper.py::Planner::test_home_and_gripper_unused_slots_match_the_recording` |
| LF06 | SHALL NOT | This work opens USB, runs on a real arm, adds dependencies, changes the excluded paths, accesses the network or writes git metadata. | review: tests use fake endpoints or pure generators; final diff and verification commands |

## Out of scope
- Stream and policy changes, new captures, real hardware validation and git publication.

## Assumptions
- Existing stopMov implements the vendor stop; PL51 is the recorded evidence for unused slots.

## Open questions
- None for the requested desk changes.

## Change log
- 2026-11-03 Baseline from the authorized task brief.
