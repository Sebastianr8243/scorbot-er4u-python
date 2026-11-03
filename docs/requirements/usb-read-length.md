# Complete USB feedback requirements
Status: baseline v1 2026-11-02 (approved task brief)

## Goal
Reject incomplete USB feedback before reused bytes reach decoding or fresh snapshots. Physical-arm behaviour remains unverified.

## Requirements
| ID | Type | Statement | Check |
|---|---|---|---|
| UR01 | SHALL | A read returning fewer bytes than the endpoint-sized receive buffer raises a `USB read failed: ...` RuntimeError through the shared legacy path before decoding. | test: `tests/test_usb_read_length.py::LegacyReadLengthTests` |
| UR02 | SHALL NOT | An incomplete or failed read publishes a fresh snapshot, changes pendant state or records reused bytes as fresh feedback. | test: `tests/test_usb_read_length.py::TrackedReadLengthTests` |
| UR03 | SHALL NOT | Packet bytes, configured sleeps or dependencies change; verification opens real USB or moves the arm. | script: required desk-only verify command; review: branch diff |

## Out of scope
- Physical-arm verification, new stop behaviour and transport timing changes.

## Assumptions
- Receive buffers are sized from `endpoint_in.wMaxPacketSize` in `Scorbot.connect`.
- Existing worker failure and vendor-home abort paths handle read exceptions.

## Open questions
- None for this change; hardware effects remain unverified.

## Change log
- 2026-11-02 Baselined from the lab-blocker task brief.
