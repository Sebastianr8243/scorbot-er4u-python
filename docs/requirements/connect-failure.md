# Failed connection motor cleanup
Status: baseline v1 2026-11-02 (task brief)

## Goal
Attempt motor-off before closing a connection whose handshake may have enabled motors.
Physical arm behavior remains unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| CF01 | SHALL | A handshake read failure after motor-on attempts the existing motor-off command before closing and raising. | test: `tests/test_robot_connect.py::HandshakeFailureTests::test_read_failure_after_motor_on_sends_motor_off_before_close` |
| CF02 | SHALL | If the cleanup write fails, connection still closes, faults, and reports unverified motor state and the physical stop. | test: `tests/test_robot_connect.py::HandshakeFailureTests::test_failed_motor_off_still_closes_and_warns` |
| CF03 | SHALL NOT | A failure before motor-on sends a new motor-off packet. | test: `tests/test_robot_connect.py::HandshakeFailureTests::test_failure_before_motor_on_only_closes` |
| CF04 | SHALL NOT | Successful handshake or normal disable packet bytes change. | script: unchanged legacy code; `pytest tests/test_transport_codec.py tests/test_dry_link.py tests/test_robot_connect.py` |

## Out of scope
- Real USB, arm motion, new protocol bytes, dependencies, or changes to successful connections.

## Assumptions
- An OUT transfer cannot prove physical motor state; the physical stop remains authoritative.

## Open questions
- None for desk implementation; controller acceptance remains unverified.

## Change log
- 2026-11-02 Baselined from the requested failure reproduction, cleanup, and verification scope.

## Verification (2026-11-02)

| Requirement | Result | Evidence |
|---|---|---|
| CF01-CF03 | pass | Focused desk run: 60 passed, 63 subtests passed in 10.32 s; includes immediate/later read failures, failed cleanup write, and failure before motor-on. |
| CF04 | pass | Legacy sources unchanged; golden codec and full-handshake desk tests pass. |

Full verification: Ruff prints `All checks passed!`; compileall exits 0; pytest
reports `1722 passed, 6 skipped, 1 warning, 12866 subtests passed in 356.15s`;
documentation check prints `docs ok`. Pytest used `--basetemp=.pytest-connect-full`
because the shared temp directory was denied. The unrelated mocked home-stop
worker warning also reproduces alone (`1 passed, 1 warning in 0.74s`).
No hardware accessed; physical behavior remains unverified.
