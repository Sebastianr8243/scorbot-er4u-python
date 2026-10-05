# Clean packet codec (USB upgrade, phase A)

> **Phase A built** (`scorbot/transport/codec.py`, wired into nothing). Phases B and C are still open.

Date: 2026-10-01. Status: design choices delegated by the user ("do this
for now"); built in the same session. Context: the upgrade path in
[MANUAL_VERIFICATION_IMPACT.md](../manual/MANUAL_VERIFICATION_IMPACT.md) and
[LAB_PLATFORM_VISION.md](../design/LAB_PLATFORM_VISION.md): build a clean
replacement for the legacy USB code next to it, prove it identical, and only
then use it.

## Goal

A small, pure, tested Python module that builds every OUT packet the legacy
`openScorbot/` code sends, **byte for byte identical**, from named fields
instead of hex strings. It documents the protocol in code and is the base for
a later driver loop (phase C: one USB thread, streaming targets, a real
software stop).

## Phases (this spec is phase A only)

| Phase | What | Needs the arm |
|---|---|---|
| **A** | Pure codec + golden tests against the legacy functions | No |
| B | Check the codec against SCORBASE captures and `motion_trace` logs; fix legacy bugs in the new code only, each with evidence | Captures |
| C | New driver loop behind `Scorbot(backend=...)`, legacy stays default until a bench A/B | Yes |

## Non-goals (phase A)

- Not wired into `Scorbot`, the simulator or any script. Nothing moves
  differently, and `scorbot.provenance.motion_source_sha256` is unchanged.
- No threads, no USB, no sleeps, no timing.
- No behaviour changes: legacy bugs are not fixed here. Inputs outside the
  domain where the legacy code is correct are **rejected** with `ValueError`,
  not reproduced (for example a count step above 65535, which the legacy
  `suma`/`resta` double-overflow).
- No IN-packet decoder: `scorbot/state.py:decode_state` already decodes
  responses.
- `openScorbot/` is not edited.

## Design

New package `scorbot/transport/` with one module `codec.py` (standard library
only; never imports `usb`, `openScorbot`, `numpy`).

Contents, following [PROTOCOL.md](../protocol/PROTOCOL.md) sections 2, 4, 6 and 8:

- `MSG_LEN = 64`, `MAX_SEQUENCE = 255`.
- `next_sequence(sequence: int) -> int`: legacy `countByte1` (1..255, 255 -> 1).
- `encode_count(value: int, sign: int) -> bytes`: the 4-byte per-joint
  field, as the legacy `detrans` / `get_encoder` produce it.
- `step_count(count: int, sign: int, step: int) -> tuple[int, int]`: legacy
  `suma` (positive step) / `resta` (negative step) one's-complement
  wrap arithmetic on the 65535 modulus, for `|step| <= 65535`; larger steps
  raise `ValueError`.
- Named command templates for every table in `libhex.py`
  (`mov_comm`, `get_msg1`, `get_msg2`, `motorson`, `motorsoff`,
  `get_scorbotoff`, `clamp`) as `bytes` constants or small functions with
  the legacy names in their docstrings.
- `build_out(sequence: int, command: bytes, joints: Mapping[str, tuple[int, int]] | None) -> bytes`:
  a full 64-byte OUT packet: sequence byte, zero bytes 1-3, command from
  offset 4, the encoder region at 12-35 (joint order from
  `scorbot.state.JOINTS`) when `joints` is given, zero padding.
- Validation: sequence in 1..255, counts in 0..65535, known joint names,
  command fits before offset 12 when a joint region is present. Bad input
  raises `ValueError` with a clear message.

Exact field formats come from the legacy code, not from this spec: where the
two disagree, the legacy bytes win and the docstring says so.

## Golden tests

`tests/test_transport_codec.py`, skipped when PyUSB is missing (importing
`openScorbot.libdef` needs it), like the existing legacy tests:

- every `libhex` template, for every sequence value 1..255, equals the
  legacy hex string after `libdef.fill_msg` and `bytes.fromhex`;
- `encode_count` equals legacy `detrans`/`get_encoder` output for counts and
  signs across the full domain (Hypothesis when installed, plus fixed seam
  cases 0, 1, 65534, 65535);
- `step_count` equals legacy `suma`/`resta` for steps 1..65535 at random and
  seam counts, and raises for larger steps;
- a full jog-step packet built by `build_out` equals what `libdef.builder` +
  `set_msg` would write, for random joint states (capture the bytes with a
  fake endpoint, never a device);
- `next_sequence` equals `countByte1` for 1..255;
- an import test: `scorbot.transport.codec` imports no `usb`, `openScorbot`
  or `numpy`.

## Docs and history

- `docs/protocol/PROTOCOL.md`: a short section naming the codec as the executable
  form of sections 2, 4, 6 and 8, and the golden tests as the proof.
- `docs/project/PROJECT_LOG.md`: entry for the work.

## Risks

- Golden tests prove equality with the legacy code, not with the controller.
  Phase B compares with real captures.
- The codec must not drift from the legacy code later; the golden tests run
  in CI and fail on any change on either side.
