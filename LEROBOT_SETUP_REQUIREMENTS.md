# LeRobot setup and network camera requirements
Status: baseline v1 2026-11-07 (authorized worker brief)

## Goal
Set up a separate LeRobot environment and use a WiFi MJPEG camera without changing arm control.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| L01 | SHALL | Setup shall print commands without running them in dry-run mode, select platform paths, and stop with step summaries on failure. | test: tests_extra/test_setup_lerobot.py |
| L02 | SHALL | Setup shall warn about Python versions other than 3.12 and Windows symlink restrictions. | test: tests_extra/test_setup_lerobot.py |
| L03 | SHALL | Network cameras shall deliver fresh decoded RGB/BGR frames with optional resizing, bounded waits and connection cleanup. | test: tests_extra/test_mjpeg_camera.py |
| L04 | SHALL | LeRobot shall construct the camera from type mjpeg. | test: tests_extra/test_mjpeg_camera.py |
| L05 | SHALL | Camera check shall accept a URL and avoid device property writes for network sources. | test: tests_extra/test_camera_cli.py and tests_extra/test_camera_source.py |
| L06 | SHALL NOT | Tests shall open a physical camera or arm, install packages, or create an actual venv. | review: new tests use mocks or a loopback HTTP server |

## Out of scope
Real hardware validation, changing arm control, new dependencies, pushing and publishing.

## Assumptions
LeRobot 0.6.1 and OpenCV are available in the supplied test venv. Real ESP32 camera behavior is unverified.

## Open questions
None for implementation. Physical connectivity remains unverified.

## Change log
- 2026-11-07: L01-L06 taken from the authorized worker brief; kept outside docs/ to respect worker ownership.

## Verification (2026-11-07)
| ID | Result | Evidence |
|---|---|---|
| L01 | pass | Setup tests: 6 passed; dry-run printed commands and summaries, exit 0. |
| L02 | pass | Setup tests cover platform paths, Python warning and denied symlink creation. |
| L03 | pass | MJPEG tests: 14 passed against loopback HTTP, including stalls, consumption, reconnect and thread cleanup. |
| L04 | pass | Camera factory test constructs MJPEGCamera from its registered config. |
| L05 | pass | Camera CLI/source: 13 passed, 2 subtests passed using fake capture. |
| L06 | pass | New tests use mocked subprocess/capture and synthetic HTTP only; repo USB backstop remained enabled. |

Combined five-file extra suite: 96 passed, 76 subtests passed. Ruff unavailable;
the canonical core gate could not start under the default Python (no pytest).
Core under the LeRobot venv remains red due to its installed tests package and
existing doctor Torch/Dill import behavior; see the worker report for counts.
