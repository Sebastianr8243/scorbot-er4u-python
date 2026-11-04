"""The vendor's digital and analog output messages, as a pure generator. Never imports ``usb``.

From the lab DLL on the desk (``docs/protocol/LEDGER.md`` PL68, PL70; unverified on the arm):

- ``SetDigitalOutput(n, on, 1)`` with the arm at rest sends ``47`` (clear buffer), then ``44`` with the DLL's
  whole 32-bit output word in bytes 8-11, bit n = output n (0 to 31). Stack 2 only sends outputs between
  moves, so it always takes this at-rest path.
- ``SetDigitalOutput(n, on, 0)`` sends no ``47``: it waits for the queue first, ``Buffers`` // 10 rounds of
  10 replies then ``Monitoring`` ms, and then sends ``44`` (``0x10019697``, PL102). ``send_queued``.
- ``SetAnalogOutput(n, value)`` sends ``41`` with the value (0 to 255) in bytes 6-7 and n (0 or 1) in bytes 8-11.

``send(first, messages)`` yields one ``vendor_home.Out`` per message, the setpoints holding the first reply's
counts, so the same ``VendorHomeSource`` and legacy runner carry it (order ``IO_ORDER``).
"""

from . import vendor_home as vh
from .pacing import ID_RING

IO_ORDER = 24
"""Command code queued to the legacy worker; it runs ``libcomm.vendor_home_run`` like orders 22 and 23."""
CLEAR, SET_DIGITAL, SET_ANALOG = 0x47, 0x44, 0x41
DIGITAL_OUTPUTS = 32
"""``SetDigitalOutput`` accepts outputs 0 to 31 (``0x10019697``)."""
ANALOG_OUTPUTS = 2
"""``SetAnalogOutput`` accepts outputs 0 and 1 (``0x10019a4c``)."""
ANALOG_MAX = 255
"""The value is an unsigned byte."""
QUEUE_ROUNDS = 45 // 10
"""``ER4CONF.INI`` ``Buffers`` (45) // 10: the rounds ``SetDigitalOutput(n, on, 0)`` waits (PL102)."""
ROUND_REPLIES = 10
"""Replies the DLL waits for in each round (``0x1003fedb`` with 10)."""
ROUND_SECONDS = 0.1
"""``USBC.INI`` ``Monitoring`` (100 ms): how long the DLL pumps messages after each round's replies."""
TOTAL_SECONDS = 5.0
"""Ours: bound on one output write: two messages, or a queued write of about 0.8 s."""


def digital_word(word: int, number: int, on: bool) -> int:
    """The output word with output ``number`` switched on or off."""
    if not 0 <= number < DIGITAL_OUTPUTS:
        raise ValueError(f"Digital output must be 0 to {DIGITAL_OUTPUTS - 1}, not {number}")
    return word | (1 << number) if on else word & ~(1 << number)


def digital_messages(word: int, *, immediate: bool = True) -> list[tuple[int, int, int]]:
    """``(command, arg16, arg32)`` for ``SetDigitalOutput``: ``47`` then ``44`` at rest, only ``44`` when not
    ``immediate`` (sent by ``send_queued``). arg32 is the word as a signed int32."""
    write = (SET_DIGITAL, 0, word - (1 << 32) if word >= 1 << 31 else word)
    return [(CLEAR, 0, 0), write] if immediate else [write]


def analog_messages(number: int, value: int) -> list[tuple[int, int, int]]:
    if not 0 <= number < ANALOG_OUTPUTS:
        raise ValueError(f"Analog output must be 0 to {ANALOG_OUTPUTS - 1}, not {number}")
    if not 0 <= value <= ANALOG_MAX:
        raise ValueError(f"Analog value must be 0 to {ANALOG_MAX}, not {value}")
    return [(SET_ANALOG, value, number)]


def advanced(last: int, echo: int) -> bool:
    """Whether ``echo`` is a newer message ID than ``last`` on the 1..255 ring, at most ``MAX_LAG`` ahead.
    A repeated, backward or missing echo is not progress (ours: the DLL counts any reply)."""
    return 1 <= last <= ID_RING and 1 <= echo <= ID_RING and 1 <= (echo - last) % ID_RING <= vh.MAX_LAG


def _check(reply, faulted):
    """Raise instead of sending once the operator or the session has said stop (ours, not the DLL's)."""
    if reply.emergency:
        raise vh.HomeError(vh.ERR_EMERGENCY, "emergency input active during the output queue wait")
    if reply.pendant_abort:
        raise vh.HomeError(vh.ERR_STOPPED, "pendant Abort pressed during the output queue wait")
    fault = faulted() if faulted is not None else None
    if fault:
        raise vh.HomeError(vh.ERR_STOPPED, f"session fault during the output queue wait: {fault}")


def send_queued(first, messages, *, faulted=None):
    """``SetDigitalOutput(n, on, 0)``: hold the first reply's counts for ``QUEUE_ROUNDS`` rounds of
    ``ROUND_REPLIES`` replies and then ``ROUND_SECONDS`` by the replies' clock, then send ``messages``.

    Ours, beyond the DLL: a round counts only replies whose echoed ID moves forward (``advanced``), so a
    controller that stops answering never drains the queue; ``VendorHomeSource`` ends that wait at its lag
    limit or ``TOTAL_SECONDS``. Emergency, pendant Abort or ``faulted()`` on any reply raises ``HomeError``
    and nothing is sent."""
    setpoints, reply = tuple(first.counts), first
    last = first.echo
    _check(reply, faulted)
    for _ in range(QUEUE_ROUNDS):
        counted = 0
        while counted < ROUND_REPLIES:
            reply = yield vh.Out(vh.NOP, 0, 0, 0, setpoints)
            _check(reply, faulted)
            if advanced(last, reply.echo):
                last, counted = reply.echo, counted + 1
        until = reply.now + ROUND_SECONDS
        while reply.now < until:
            reply = yield vh.Out(vh.NOP, 0, 0, 0, setpoints)
            _check(reply, faulted)
    return (yield from send(first, messages))


def send(first, messages):
    """Send ``messages`` with every setpoint at the first reply's counts. ``first`` is a ``vendor_home.Reply``."""
    setpoints = tuple(first.counts)
    for command, arg16, arg32 in messages:
        yield vh.Out(command, 0, arg16, arg32, setpoints)
    return len(messages)       # a result, so ``VendorHomeSource.ok`` holds
