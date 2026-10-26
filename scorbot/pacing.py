"""Vendor-style read-delay pacing from the echoed message ID. Pure: stdlib only, no USB, no sleeps.

The vendor DLL waits 1 ms before the next message when (sent ID minus the ID the reply echoes) is below
the queue limit, and about half the PCPeriod otherwise. Off unless ``Scorbot.start_stream`` is given
``adaptive_pacing=True``; the numbers are the vendor's (docs/design/STACK2_RESEARCH.md), unverified on the arm.
"""

ID_RING = 255
"""The legacy message IDs run 1 to 255 and wrap to 1 (``libdef.countByte1``); an IN packet's byte 0 echoes the ID it answers."""
QUEUE_LIMIT = 45
"""The vendor INI's ``Buffers`` for normal moves (HomingBuffers 20, TPBuffers 6, ManualBuffers 4)."""
FAST_DELAY_S = 0.001


def echo_lag(sent_id, reply):
    """Sent ID minus the ID the reply echoes, on the 1..255 ring; ``None`` if the reply echoes nothing usable."""
    echo = reply[0] if reply else 0
    if not 1 <= echo <= ID_RING:
        return None
    return (sent_id - echo) % ID_RING


def next_read_delay(lag, limit, period_s):
    """Seconds to wait before the next message: 1 ms while the controller is within ``limit``, else half the period.

    An unknown lag (``None``) gets the slow wait: never faster than the vendor when the echo is unreadable.
    """
    if lag is not None and lag < limit:
        return FAST_DELAY_S
    return period_s / 2


def make_pace(limit=QUEUE_LIMIT, period_s=0.016):
    """A ``pace(sent_id, reply) -> seconds`` for the stream order; ``period_s`` is the controller's PCPeriod."""
    return lambda sent_id, reply: next_read_delay(echo_lag(sent_id, reply), limit, period_s)
