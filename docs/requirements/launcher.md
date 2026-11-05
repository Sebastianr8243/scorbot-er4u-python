# Stack 2 launcher requirements
Status: baseline v1 2026-11-04 (implementation brief and existing launcher tests)

## Goal
Give students one command for setup checks and the existing Stack 2 tools.
Keep all existing authorization and cleanup behavior.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| CL01 | SHALL NOT | Doctor opens a controller, starts a child process, or writes files. | test: `tests.test_cli.DoctorReadOnlyTests` |
| CL02 | SHALL | Every launcher target receives the user's arguments unchanged, including help. | test: `tests.test_cli.ForwardingTests` |
| CL03 | SHALL NOT | The launcher adds authorization flags or bypasses the wrapped tools' gates. | test: `tests.test_cli.ForwardingTests`, `tests.test_cli.SafetyGateTests` |
| CL04 | SHALL | Ctrl+C allows child cleanup to finish without killing the child. | test: `tests.test_cli.ChildProcessTests` |
| CL05 | SHALL | Doctor reports checks, fixes, counts and exit status in text and JSON, including strict warnings. | test: `tests.test_cli.DoctorOutputTests` |
| CL06 | SHALL | Calibration validation uses the existing loader with the requested robot ID. | test: `tests.test_cli.CalibrationCheckTests` |
| CL07 | SHALL | Existing preflight output remains unchanged. | test: `tests.test_cli.PreflightCompatibilityTests`, `tests.test_preflight`, `tests.test_preflight_more` |

## Out of scope
- Changes to wrapped scripts, motion paths, drivers or installed dependencies.
- Running LeRobot recording, real-arm validation or the full test suite.

## Assumptions
- Script targets run from a source checkout installed in editable mode.
- The LeRobot tools use a separate environment as documented in the plugin README.

## Open questions
None.

## Change log
- 2026-11-04: CL01-CL07 baseline follows the approved implementation brief and the existing test contract.
