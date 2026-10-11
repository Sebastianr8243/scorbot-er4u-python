"""Test helpers for ``scorbot.vendor_home``: drive the generator against the arm model through real packets.

The model itself lives in ``scorbot/vendor_home_model.py`` (the simulator uses it too), so the recording
test pins the same model the simulator runs.
"""

from scorbot import vendor_home as vh
from scorbot.vendor_home_model import BANDS, DRIVE_GAIN, FOLLOW_STEP, Plant, s32  # noqa: F401


def run_generator(make, plant, *, cycle_s=0.016, max_cycles=200000, on_cycle=None):
    """Drive any ``vendor_home`` generator (``make(first_reply)``) against ``plant`` through real packets.

    Returns ``(outs, result)``. If the generator raises ``HomeError`` the error gets an ``outs``
    attribute with every message sent up to and including the stop it yielded first.
    ``on_cycle(n, plant)`` runs before each exchange so a test can inject a fault.
    """
    now = 0.0
    first = vh.decode_reply(plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1)), now)
    home = make(first)
    outs, sequence = [], 1
    out = next(home)
    while True:
        if len(outs) >= max_cycles:
            raise AssertionError("home did not finish")
        if on_cycle:
            on_cycle(len(outs), plant)
        outs.append(out)
        sequence = sequence % 255 + 1
        now += cycle_s
        try:
            out = home.send(vh.decode_reply(plant.exchange(vh.encode_out(out, sequence)), now))
        except StopIteration as stop:
            return outs, stop.value
        except vh.HomeError as error:
            error.outs = outs
            raise


def run(plant, *, version=0, axes=None, cycle_s=0.016, max_cycles=200000, on_cycle=None):
    """Drive the whole ``vendor_home`` against ``plant``. Returns ``(outs, result)``."""
    return run_generator(lambda first: vh.vendor_home(first, version=version, axes=axes), plant,
                         cycle_s=cycle_s, max_cycles=max_cycles, on_cycle=on_cycle)


def commands(outs):
    """The non-0D commands, consecutive repeats collapsed, as ``[cmd, mask, arg16, arg32]`` lists."""
    items = [[o.cmd, o.mask, o.arg16, o.arg32] for o in outs if o.cmd != vh.NOP]
    return [item for i, item in enumerate(items) if i == 0 or item != items[i - 1]]
