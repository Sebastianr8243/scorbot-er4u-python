# Teach-mode stream stop requirements
Status: baseline v1 2026-11-02 (approved task brief)

## Goal
End an active Stack 2 stream when a reply turns teach mode on, using the normal stream stop. Physical-arm behavior is unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| TS01 | SHALL | A stream that starts in Auto shall stop on the first reply enabling teach mode, without another target setpoint, using exactly the normal stop bytes. | test: `tests_extra/test_streaming.py::LegacyStreamTests::test_teach_mode_mid_stream_sends_exactly_the_normal_stop_bytes` |
| TS02 | SHALL | The stopped stream shall report teach mode as its reason, remain stopped, and refuse a new stream while teach mode stays on. | test: `tests/test_teach_mode.py::StreamRefusalTests::test_teach_mode_mid_stream_stops_without_the_caller_and_refuses_restart` |
| TS03 | SHALL | PolicyArm's active stream shall stop when teach mode turns on and refuse further actions. | test: `tests/test_teach_mode.py::PolicyArmTeachTests::test_teach_mode_mid_stream_stops_policy_actions` |
| TS04 | SHALL NOT | Verification shall open real USB or move the arm. | review: tests use SimulatedScorbot or fake endpoints only; pytest blocks real USB discovery in `conftest.py` |

## Out of scope
- New packet sequences, commands, dependencies, and changes to non-streaming motions.
- Physical-arm validation (unverified).

## Assumptions
- The teach-mode decoding from PR #99 is the existing source of truth.
- The existing normal stop path is appropriate; its effect on the physical arm is unverified.

## Open questions
None for the desk implementation.

## Change log
- 2026-11-02: TS01-TS04 recorded from the approved task brief.
