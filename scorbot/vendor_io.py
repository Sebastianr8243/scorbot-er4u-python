"""The vendor's digital and analog output messages, as a pure generator. Never imports ``usb``.

From the lab DLL on the desk (``docs/protocol/LEDGER.md`` PL68, PL70; unverified on the arm):

- ``SetDigitalOutput(n, on, 1)`` with the arm at rest sends ``47`` (clear buffer), then ``44`` with the DLL's
  whole 32-bit output word in bytes 8-11, bit n = output n (0 to 31). Stack 2 only sends outputs between
  moves, so it always takes this at-rest path.
- ``SetAnalogOutput(n, value)`` sends ``41`` with the value (0 to 255) in bytes 6-7 and n (0 or 1) in bytes 8-11.

``send(first, messages)`` yields one ``vendor_home.Out`` per message, the setpoints holding the first reply's
counts, so the same ``VendorHomeSource`` and legacy runner carry it (order ``IO_ORDER``).
"""

from . import vendor_home as vh

IO_ORDER = 24
"""Command code queued to the legacy worker; it runs ``libcomm.vendor_home_run`` like orders 22 and 23."""
CLEAR, SET_DIGITAL, SET_ANALOG = 0x47, 0x44, 0x41
DIGITAL_OUTPUTS = 32
"""``SetDigitalOutput`` accepts outputs 0 to 31 (``0x10019697``)."""
ANALOG_OUTPUTS = 2
"""``SetAnalogOutput`` accepts outputs 0 and 1 (``0x10019a4c``)."""
ANALOG_MAX = 255
"""The value is an unsigned byte."""
TOTAL_SECONDS = 5.0
"""Ours: bound on one output write, two messages."""


def digital_word(word: int, number: int, on: bool) -> int:
    """The output word with output ``number`` switched on or off."""
    if not 0 <= number < DIGITAL_OUTPUTS:
        raise ValueError(f"Digital output must be 0 to {DIGITAL_OUTPUTS - 1}, not {number}")
    return word | (1 << number) if on else word & ~(1 << number)


def digital_messages(word: int) -> list[tuple[int, int, int]]:
    """``(command, arg16, arg32)`` for the at-rest ``SetDigitalOutput``. arg32 is the word as a signed int32."""
    return [(CLEAR, 0, 0), (SET_DIGITAL, 0, word - (1 << 32) if word >= 1 << 31 else word)]


def analog_messages(number: int, value: int) -> list[tuple[int, int, int]]:
    if not 0 <= number < ANALOG_OUTPUTS:
        raise ValueError(f"Analog output must be 0 to {ANALOG_OUTPUTS - 1}, not {number}")
    if not 0 <= value <= ANALOG_MAX:
        raise ValueError(f"Analog value must be 0 to {ANALOG_MAX}, not {value}")
    return [(SET_ANALOG, value, number)]


def send(first, messages):
    """Send ``messages`` with every setpoint at the first reply's counts. ``first`` is a ``vendor_home.Reply``."""
    setpoints = tuple(first.counts)
    for command, arg16, arg32 in messages:
        yield vh.Out(command, 0, arg16, arg32, setpoints)
    return len(messages)       # a result, so ``VendorHomeSource.ok`` holds
