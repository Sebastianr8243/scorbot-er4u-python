"""Clean replacement for the legacy USB code, built next to it (USB upgrade).

Phase A holds only :mod:`scorbot.transport.codec`, a pure packet codec proven
byte-identical to ``openScorbot/`` by ``tests/test_transport_codec.py``.
Nothing here opens USB, and nothing in the SDK uses it yet.
"""
