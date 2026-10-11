"""Clean replacement for the legacy USB code, built next to it (USB upgrade).

Phase A holds only :mod:`scorbot.transport.codec`, a pure packet codec proven
byte-identical to ``openScorbot/`` by ``tests/test_transport_codec.py``.
Nothing here opens USB, and nothing in the SDK uses it yet.

:mod:`scorbot.transport.device_file` is the link through Intelitek's driver (the vendor DLL's own
transport); also not used by the SDK yet, and never run against a device.
"""
