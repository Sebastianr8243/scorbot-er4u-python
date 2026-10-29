"""The per-message helpers in libdef must not re-parse data.json.

Each USB message used to cost several json parses (about 0.3 ms each, measured) on top of the 8 to 19 ms
sleeps. Only the TIME_OUT_R read inside the write-to-read gap stays, because shortening that gap needs a
capture (openScorbot/README.md). No USB is opened.
"""

import importlib.util
import unittest
from unittest import mock

from scorbot import Scorbot

HAVE_USB = importlib.util.find_spec("usb") is not None


@unittest.skipUnless(HAVE_USB, "pyusb is required by the legacy modules")
class LibdefConfigReads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = Scorbot()._legacy("libdef")

    def reads(self, fn, *args):
        with mock.patch.object(self.lib.conf, "readData", wraps=self.lib.conf.readData) as spy:
            result = fn(*args)
        return result, [call.args for call in spy.call_args_list]

    def test_count_byte_reads_no_config(self):
        self.assertEqual(self.reads(self.lib.countByte1, 5), (6, []))
        self.assertEqual(self.reads(self.lib.countByte1, self.lib.MAX_COUNT)[0], 1)

    def test_get_media_and_get_encoder_read_no_config(self):
        buffer = [128] * 64
        _, calls = self.reads(self.lib.get_media, buffer, [0] * 6)
        self.assertEqual(calls, [])
        _, calls = self.reads(self.lib.get_encoder, buffer, [0] * 6)
        self.assertEqual(calls, [])

    def test_set_msg_reads_only_the_read_timeout(self):
        epout, epin = mock.Mock(), mock.Mock()
        with mock.patch.object(self.lib.time, "sleep"):
            _, calls = self.reads(self.lib.set_msg, "48", epout, epin, bytearray(64), 0.008, 0.012)
        self.assertEqual(calls, [("general", "TIME_OUT_R")])
        sent = epout.write.call_args.args[0]
        self.assertEqual(len(sent) * 2, self.lib.conf.readData("general", "MSG_LEN"))  # MSG_LEN counts hex characters
        self.assertEqual(epout.write.call_args.args[1], self.lib.conf.readData("general", "TIME_OUT_W"))


if __name__ == "__main__":
    unittest.main()
