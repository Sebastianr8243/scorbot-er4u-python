# Pendant Abort and emergency feedback requirements
Status: baseline v1 2026-11-02 (authorized by the lab-blocker task brief)

## Goal
End motion when Abort or emergency feedback is received and lock the affected session.
Verify on desk fakes and simulators only; physical-arm behavior is unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| AB01 | SHALL | Pendant Abort (reply byte 3 = 0x21 or 0xA1), held at entry or pressed during vendor Home, shall end Home through the existing stop sequence and latch the session. | test: `pytest tests/test_vendor_home_robot.py -k pendant_abort` |
| AB02 | SHALL | Emergency feedback in any reply during ordinary motion shall stop the motion, latch a fault, invalidate enabled/home state and queue motors-off. | test: `pytest tests/test_emergency_feedback.py` |
| AB03 | SHALL | Emergency feedback during a stream shall latch the session and send motors-off with default stream settings. | test: `pytest tests_extra/test_streaming.py -k emergency` |
| AB04 | SHALL NOT | Verification shall open a real USB device or move the arm. | script: the task's full pytest command runs under `conftest.py`'s USB guard; new tests use simulator/fake endpoints only |

## Out of scope
- A new stop sequence, physical-arm verification, DLL execution or raw vendor artifacts.
- Changing ordinary pendant-stop recovery outside vendor Home.

## Assumptions
- The existing software stop and fault paths require a responsive worker and link.
- Physical-arm behavior is unverified; the physical stop remains authoritative.

## Open questions
None for this change.

## Change log
- 2026-11-02 Baseline records the explicit task brief; fixes remain two separate commits.
