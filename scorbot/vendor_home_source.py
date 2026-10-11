"""Adapter between ``vendor_home`` (a generator) and the legacy worker's message loop. Pure: no USB, no sleeps.

The legacy command worker runs one order by exchanging a message, reading the reply, and asking a
``source`` what to send next (the way the stream order does). ``VendorHomeSource`` is that source:

    source(reply_bytes, sequence) -> ("send", packet) | ("end",) | ("stop",)

``reply_bytes`` is the latest IN packet; ``sequence`` is the OUT message ID to use. The worker only
does I/O. Everything else, including what a stop means, lives here:

* A stop request, a cancel (the session is dying), the total deadline or an unreadable reply is
  thrown into the generator as a ``HomeError``. The generator answers with its abort messages (``47``,
  ``4D 0`` to every motor, hold, ``4F FF 53``); the source keeps feeding them out, and only then ends
  with ``("stop",)``. So an interrupted home still stops the motors first.
* ``("end",)`` means the home finished (``result`` is set). ``("stop",)`` means it failed
  (``error`` is set); the worker then switches the motors off and reports failure.
* The source never raises: an exception in the worker thread would kill it mid-home.

None of this is an emergency stop: it needs a live USB link and a responsive worker.
"""

import time

from . import vendor_home as vh

VENDOR_HOME_ORDER = 22
"""Command code queued to the worker (21 is the stream, 18 the legacy home)."""
TOTAL_SECONDS = 800.0
"""Bound on the whole home: every axis' ``max_time`` in both passes adds up to about 730 seconds."""


class VendorHomeSource:
    """One vendor home, seen from the worker thread. Build a new one for every home."""

    def __init__(self, *, version=0, axes=None, stop_event=None, cancel_event=None,
                 clock=time.monotonic, total_seconds=TOTAL_SECONDS):
        self.version = version
        self.axes = axes
        self.stop_event = stop_event
        self.cancel_event = cancel_event
        self.clock = clock
        self.total_seconds = total_seconds
        self.generator = None
        self.started = None
        self.messages = 0
        self.interrupted = None      # (code, message) of the interrupt that was thrown in, if any
        self.last_reply = None       # the latest reply that decoded, kept for an abort that meets bad ones
        self.result = None           # HomeResult after ("end",)
        self.error = None            # the HomeError or other exception after ("stop",)
        self._thrown = False         # whether ``interrupted`` has been thrown into the generator yet
        self.aborting = False        # the last message the generator gave out belonged to its abort
        self.abandoned = False       # the worker gave up on a dead link before the abort could finish

    @property
    def ok(self):
        return self.result is not None and self.error is None

    def __call__(self, reply_bytes, sequence):
        try:
            return self._step(bytes(reply_bytes), sequence)
        except Exception as exc:                 # the worker must never see an exception from here
            self.error = self.error or exc
            return ("stop",)

    def interrupt(self, code, message):
        """Ask for the abort from outside (the worker saw a USB error). The next step throws it into the generator."""
        if self.interrupted is None:
            self.interrupted = (code, message)
            self._thrown = False

    def abandon(self):
        """The worker could not even send the abort messages (a dead link): record why, and that the stop is unsent."""
        self.abandoned = True
        if self.error is None and self.interrupted is not None:
            self.error = vh.HomeError(*self.interrupted)

    def _interruption(self, now):
        if self.cancel_event is not None and self.cancel_event.is_set():
            return vh.ERR_STOPPED, "the session was cancelled"
        if self.stop_event is not None and self.stop_event.is_set():
            return vh.ERR_STOPPED, "a stop was requested"
        if now - self.started > self.total_seconds:
            return vh.ERR_TIMEOUT, f"the home took longer than {self.total_seconds:.0f} seconds in all"
        return None

    def _step(self, reply_bytes, sequence):
        now = self.clock()
        if self.generator is None:
            self.started = now
            first = self.last_reply = vh.decode_reply(reply_bytes, now)
            self.generator = vh.vendor_home(first, version=self.version, axes=self.axes)
            return self._send(next(self.generator), sequence)
        reply = None
        try:
            if self.aborting:
                # The home is already stopping the motors (its own fault, or an interrupt thrown earlier):
                # nothing may be thrown into the abort, or its stop messages are cut short. Feed it the
                # latest reply that decoded, whatever this one is.
                try:
                    reply = self.last_reply = vh.decode_reply(reply_bytes, now)
                except ValueError:
                    reply = self.last_reply
                thrown = None
            else:
                if self.interrupted is None:
                    self.interrupted = self._interruption(now)
                    self._thrown = False
                thrown = self.interrupted if self.interrupted is not None and not self._thrown else None
                if thrown is None:
                    try:
                        reply = self.last_reply = vh.decode_reply(reply_bytes, now)
                    except ValueError as exc:
                        if self.interrupted is None:
                            self.interrupted = thrown = (vh.ERR_BAD_REPLY, f"unreadable reply: {exc}")
                            self._thrown = False
                        else:
                            reply = self.last_reply
            if thrown is not None:
                self._thrown = True
                out = self.generator.throw(vh.HomeError(*thrown))
            else:
                out = self.generator.send(reply)
        except StopIteration as stop:
            self.result = stop.value
            return ("end",)
        except vh.HomeError as error:
            self.error = self.error or error
            return ("stop",)
        self.aborting = bool(out.aborting)
        self.messages += 1
        return self._send(out, sequence)

    @staticmethod
    def _send(out, sequence):
        return ("send", vh.encode_out(out, sequence))
