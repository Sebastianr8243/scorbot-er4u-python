"""Digital and analog outputs: the ``Scorbot`` methods that write them (``scorbot/vendor_io.py``).

They send what the lab DLL sends (``docs/protocol/LEDGER.md`` PL68, PL70). Unverified on the arm: never wire
an output to anything that moves until a bench log shows what the line does.
"""

from . import vendor_io
from .errors import ScorbotError
from .vendor_home_source import VendorHomeSource


class IoMixin:

    _digital_outputs = 0

    @property
    def digital_outputs(self) -> int:
        """Stack 2's copy of the output word, bit N = output N, as last written. Like the DLL's, it is not read
        back from the controller, so it starts at 0 on each new ``Scorbot``."""
        return self._digital_outputs

    def set_digital_output(self, number: int, on: bool, *, immediate: bool = True) -> None:
        """Switch digital output ``number`` (0 to 31) on or off. Sends ``47`` then ``44`` with the whole word.

        ``immediate=False`` is the DLL's ``SetDigitalOutput(n, on, 0)``: no ``47``; about 0.8 s of held
        setpoints (the DLL's queue wait, PL102), then ``44``. Unverified on the arm.
        Needs a connection, not enabled motors. Refused while a stream is active; it waits for a jog to end.
        """
        immediate = bool(immediate)
        with self._motion_lock:     # 44 replaces the whole word: no other write in between
            word = vendor_io.digital_word(self._digital_outputs, number, bool(on))
            messages = vendor_io.digital_messages(word, immediate=immediate)
            if immediate:
                self._io(messages)
            else:
                self._io(messages, send=vendor_io.send_queued)
            self._digital_outputs = word
        self._record("digital_output", number=number, on=bool(on), word=word, immediate=immediate)

    def set_analog_output(self, number: int, value: int) -> None:
        """Set analog output ``number`` (0 or 1) to ``value`` (0 to 255). Sends ``41``."""
        messages = vendor_io.analog_messages(number, value)
        self._io(messages)
        self._record("analog_output", number=number, value=value)

    def _io(self, messages, send=vendor_io.send) -> None:
        with self._motion_lock:
            self._refuse_if_streaming()
            if self._device is None:
                raise ScorbotError("Not connected")
            # The messages hold every axis at the first reply's counts, so the arm must be at rest
            # (the DLL sends 47 only then, 0x10024d1d). Latches a fault if it is not, like the gripper.
            self._settled_after_stop(self._motion_state(),
                                     fault="The arm was not at rest before an output write")
            # No stop event: an output write is not motion, so a pending stop request must not cancel it.
            source = VendorHomeSource(start=lambda first: send(first, messages),
                                      cancel_event=self._cancel_event,
                                      total_seconds=vendor_io.TOTAL_SECONDS)
            self._command([vendor_io.IO_ORDER, source, 0], timeout=vendor_io.TOTAL_SECONDS + 30.0)
            if not source.ok:
                raise ScorbotError(f"Output write failed: {source.error}")
