"""BACKLOG 15: the sync worker's pause marker is the configured MAX_COUNT, the value libcomm puts on the queue.

libcomm.py reads MAX_COUNT from the config and puts it on the sync queue to pause the sync worker while the
command worker holds the sequence byte. libsync.syncro compared against a literal 256, so an edited
MAX_COUNT would have left two workers holding the byte at once. No message is built here: libdef and libhex
are replaced, so nothing can reach USB.
"""

import queue
import unittest
from unittest import mock

from scorbot import Scorbot


class PauseMarkerTests(unittest.TestCase):
    def setUp(self):
        self.sync = Scorbot()._legacy("libsync")
        self.exit = self.sync.conf.readData("general", "EXIT")

    def run_syncro(self, value, **patches):
        """Put ``value`` and then EXIT on the sync queue and run the worker; returns (libdef, sleep, cola_read)."""
        cola_sync, cola_read = queue.Queue(), queue.Queue()
        cola_sync.put(value)
        cola_sync.put(self.exit)
        cola_read.put([0])                     # what a sent message would consume; an unexpected send must not block
        with mock.patch.object(self.sync, "libdef") as libdef, mock.patch.object(self.sync, "libhex"), \
                mock.patch.object(self.sync.time, "sleep") as sleep:
            for name, new in patches.items():
                stack = mock.patch.object(self.sync, name, new, create=True)
                stack.start()
                self.addCleanup(stack.stop)
            self.sync.syncro(cola_sync, cola_read, None, None, bytearray(64))
        return libdef, sleep, cola_read

    def test_the_default_marker_pauses_without_sending(self):
        libdef, sleep, cola_read = self.run_syncro(self.sync.conf.readData("general", "MAX_COUNT"))
        sleep.assert_called_once_with(self.sync.READ)
        libdef.set_msg.assert_not_called()
        self.assertEqual(cola_read.qsize(), 1)           # the averaged vector was not touched

    def test_a_sequence_byte_still_sends_one_message(self):
        libdef, sleep, _ = self.run_syncro(5)
        libdef.set_msg.assert_called_once()
        sleep.assert_not_called()

    def test_a_configured_marker_other_than_256_is_honoured(self):
        libdef, sleep, cola_read = self.run_syncro(300, MAX_COUNT=300)
        sleep.assert_called_once_with(self.sync.READ)
        libdef.set_msg.assert_not_called()
        self.assertEqual(cola_read.qsize(), 1)

    def test_the_marker_is_read_from_the_configuration_at_import(self):
        # Patching syncro's MAX_COUNT (above) cannot tell a configured value from a literal, so load a separate
        # copy of libsync while the config says 300. The copy is never registered in sys.modules.
        import importlib.util

        conf = self.sync.conf
        real = conf.readData

        def read(section, key, *args, **kwargs):
            return 300 if (section, key) == ("general", "MAX_COUNT") else real(section, key, *args, **kwargs)

        spec = importlib.util.spec_from_file_location("libsync_with_marker_300", self.sync.__file__)
        copy = importlib.util.module_from_spec(spec)
        with mock.patch.object(conf, "readData", side_effect=read):
            spec.loader.exec_module(copy)
        self.assertEqual(copy.MAX_COUNT, 300)
        self.assertEqual(self.sync.MAX_COUNT, real("general", "MAX_COUNT"))     # the real module is untouched


if __name__ == "__main__":
    unittest.main()
