"""The torque (PWM) readout: the ``Scorbot`` method that asks for it (``scorbot/vendor_torque.py``).

It sends what the lab DLL's ``ShowTorque`` sends (``docs/protocol/LEDGER.md`` PL74). Read-only: every message
holds the measured counts as setpoints, like an idle message. What the number means on the arm is unverified.
"""

from . import vendor_torque
from .errors import ScorbotError
from .vendor_home_source import VendorHomeSource


class TorqueMixin:

    def read_torque(self, axis) -> int:
        """The controller's drive (PWM) reading for ``axis`` (0 to 5, or ``"A"`` for all), as the DLL reports it:
        reply bytes 11-14 minus 244. Units and sign are unverified on the arm.

        Needs a connection, not enabled motors. Refused while a stream is active; it waits for a jog to end.
        """
        axis_mask = vendor_torque.mask(axis)
        with self._motion_lock:
            self._refuse_if_streaming()
            if self._device is None:
                raise ScorbotError("Not connected")
            # The messages hold every axis at the first reply's counts, so the arm must be at rest.
            self._settled_after_stop(self._motion_state(),
                                     fault="The arm was not at rest before a torque reading")
            # No stop event: a reading is not motion, so a pending stop request must not cancel it.
            source = VendorHomeSource(start=lambda first: vendor_torque.read(first, axis_mask),
                                      cancel_event=self._cancel_event,
                                      total_seconds=vendor_torque.TOTAL_SECONDS)
            self._command([vendor_torque.TORQUE_ORDER, source, 0], timeout=vendor_torque.TOTAL_SECONDS + 30.0)
        if source.error is not None:
            raise ScorbotError(f"Torque reading failed: {source.error}")
        if source.result is None:
            raise ScorbotError(f"The controller did not answer 57 within {vendor_torque.MAX_MESSAGES} messages")
        self._record("torque_read", axis=axis, mask=axis_mask, value=source.result)
        return source.result
