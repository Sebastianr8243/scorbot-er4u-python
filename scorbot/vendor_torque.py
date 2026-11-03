"""The vendor's torque (PWM) readout, ``ShowTorque``, as a pure generator. Never imports ``usb``.

From the lab DLL (``docs/protocol/LEDGER.md`` PL74; desk recording 2026-11-02; unverified on the arm):

- While ``ShowTorque(axis, callback)`` is open, the comm thread sends ``57`` with the axis mask in byte 5 in
  place of every idle ``0D`` (``0x1003ccc8``), with the same setpoint table. Mask: bit N for axis N, ``3F`` for
  ``'A'``. Bytes 6-11 stay zero.
- It takes the value only from a reply whose byte 1 echoes ``57``: the signed 32-bit bytes 11-14, minus 244
  (``0xF4``). One value per reply, whatever the mask. On the desk, replies of 1000 + mask gave 757, 760 and 819,
  and other replies' bytes 11-14 never reached the callback.

``read(first, mask)`` sends ``57`` until a reply to one of its own messages echoes ``57``, the setpoints holding
the first reply's counts, so it carries what an idle message would. It returns the value, or ``None`` after
``MAX_MESSAGES`` or at the source's deadline with no answer, so a missing answer never makes the runner switch
the motors off (a cancel, a USB error or a stale link still do). ``VendorHomeSource`` and the legacy runner
carry it (order ``TORQUE_ORDER``). What the number is on the arm (units, sign, which axis for a multi-axis
mask) is unverified.
"""

from . import vendor_home as vh

TORQUE_ORDER = 25
"""Command code queued to the legacy worker; it runs ``libcomm.vendor_home_run`` like orders 22 and 23."""
SHOW_TORQUE = 0x57
RESULT_OFFSET = 244
"""The DLL subtracts ``0xF4`` from reply bytes 11-14 before it stores the reading (``0x1003ccc8``)."""
ALL_ARM_AXES = 0x3F
"""The mask the DLL sends for ``'A'``: the six arm axes, gripper included."""
MAX_MESSAGES = 80
"""Ours: ``57`` messages to send before giving up, above ``vendor_home.MAX_LAG`` (60)."""
TOTAL_SECONDS = 5.0
"""Ours: bound on one reading."""


def mask(axis) -> int:
    """The byte 5 mask: axis 0 to 5 (base to gripper) or ``"A"`` for all of them."""
    if axis == "A":
        return ALL_ARM_AXES
    if type(axis) is not int or not 0 <= axis <= vh.GRIPPER:
        raise ValueError(f'Torque axis must be 0 to {vh.GRIPPER} or "A", not {axis!r}')
    return 1 << axis


def read(first, axis_mask: int):
    """Send ``57`` until our own message is answered. ``first`` is a ``vendor_home.Reply``, never decoded."""
    setpoints = tuple(first.counts)
    for sent in range(1, MAX_MESSAGES + 1):
        try:
            reply = yield vh.Out(SHOW_TORQUE, axis_mask, 0, 0, setpoints)
        except vh.HomeError as error:
            if error.code == vh.ERR_TIMEOUT:    # the source's deadline: no answer, not a fault
                return None
            raise                               # a cancel, a USB error, a stale link: the runner's stop path
        # ``lag`` counts our messages still unanswered, so lag < sent means this reply answers one of ours.
        if reply.command == SHOW_TORQUE and reply.lag < sent:
            return reply.result - RESULT_OFFSET
    return None
