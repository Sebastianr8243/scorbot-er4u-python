"""scorbot.transport.device_file: the Intelitek-driver link, tested against a fake Win32 layer.

The real layer (``Win32Api``) is never constructed here: opening ``\\\\.\\ERUSBDevice0`` on a PC with
the controller attached would reach the device. Everything below uses ``FakeApi``.
"""

from array import array
import threading
import time
import unittest

from scorbot.transport import device_file as df


class FakeApi:
    """Stands in for kernel32. ``replies`` is a list of bytes (or an exception) handed out by ``read_file``."""

    def __init__(self, replies=(), *, create_error=None, written=None, block=False):
        self.replies = list(replies)
        self.create_error = create_error
        self.written_override = written
        self.block = block
        self.calls = []
        self.sent = []
        self.unblock = threading.Event()

    def create_file(self, path):
        self.calls.append(("create", path))
        if self.create_error is not None:
            raise self.create_error
        return 7

    def write_file(self, handle, data):
        self.calls.append(("write", handle, len(data)))
        self.sent.append(bytes(data))
        return len(data) if self.written_override is None else self.written_override

    def read_file(self, handle, size):
        self.calls.append(("read", handle, size))
        if self.block:
            self.unblock.wait(5)
            raise df.Win32Error(df.ERROR_OPERATION_ABORTED, "aborted")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def cancel_io(self, thread_id):
        self.calls.append(("cancel", thread_id))
        self.unblock.set()

    def close_handle(self, handle):
        self.calls.append(("close", handle))


def link(api, **kwargs):
    return df.DeviceLink(api=api, **kwargs)


class OpenTests(unittest.TestCase):
    def test_import_opens_nothing_and_default_path_is_the_vendor_driver_name(self):
        self.assertEqual(df.DEFAULT_PATH, "\\\\.\\ERUSBDevice0")

    def test_open_goes_to_the_given_path_once(self):
        api = FakeApi()
        link(api, path="\\\\.\\ERUSBDevice3")
        self.assertEqual(api.calls, [("create", "\\\\.\\ERUSBDevice3")])

    def test_a_sharing_violation_names_the_other_program(self):
        api = FakeApi(create_error=df.Win32Error(df.ERROR_SHARING_VIOLATION, "busy"))
        with self.assertRaises(df.DeviceLinkError) as caught:
            link(api)
        self.assertIn("SCORBASE", str(caught.exception))

    def test_a_missing_device_says_the_driver_or_controller_is_absent(self):
        api = FakeApi(create_error=df.Win32Error(df.ERROR_FILE_NOT_FOUND, "no file"))
        with self.assertRaises(df.DeviceLinkError) as caught:
            link(api)
        self.assertIn("driver", str(caught.exception).lower())


class WriteTests(unittest.TestCase):
    def test_a_64_byte_packet_is_written_whole(self):
        api = FakeApi()
        packet = bytes(range(64))
        self.assertEqual(link(api).endpoint_out.write(packet, 1500), 64)
        self.assertEqual(api.sent, [packet])

    def test_a_short_write_is_an_error(self):
        api = FakeApi(written=63)
        with self.assertRaises(df.DeviceLinkError):
            link(api).endpoint_out.write(bytes(64), 1500)

    def test_only_64_byte_packets_are_accepted(self):
        api = FakeApi()
        with self.assertRaises(ValueError):
            link(api).endpoint_out.write(bytes(63), 1500)
        self.assertEqual(api.sent, [])


class ReadTests(unittest.TestCase):
    def test_a_read_fills_the_callers_buffer_in_place_and_returns_the_count(self):
        reply = bytes(range(64))
        for buffer in (array("B", bytes(64)), bytearray(64)):
            with self.subTest(type=type(buffer).__name__):
                self.assertEqual(link(FakeApi([reply])).endpoint_in.read(buffer, 1500), 64)
                self.assertEqual(bytes(buffer), reply)

    def test_the_endpoint_reports_the_packet_size_the_sdk_asks_for(self):
        self.assertEqual(link(FakeApi()).endpoint_in.wMaxPacketSize, 64)

    def test_zero_byte_reads_are_retried_as_the_vendor_dll_does(self):
        reply = bytes([9]) * 64
        api = FakeApi([b"", b"", reply])
        buffer = bytearray(64)
        self.assertEqual(link(api).endpoint_in.read(buffer, 1500), 64)
        self.assertEqual(bytes(buffer), reply)

    def test_ten_zero_byte_reads_in_a_row_are_an_error(self):
        with self.assertRaises(df.DeviceLinkError):
            link(FakeApi([b""] * 10)).endpoint_in.read(bytearray(64), 1500)

    def test_a_partial_packet_is_an_error(self):
        with self.assertRaises(df.DeviceLinkError):
            link(FakeApi([bytes(10)])).endpoint_in.read(bytearray(64), 1500)

    def test_a_win32_error_on_read_becomes_a_device_error(self):
        api = FakeApi([df.Win32Error(31, "device not functioning")])
        with self.assertRaises(df.DeviceLinkError):
            link(api).endpoint_in.read(bytearray(64), 1500)


class TimeoutTests(unittest.TestCase):
    def test_a_read_that_never_answers_is_cancelled_and_raises_timeout(self):
        api = FakeApi(block=True)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            link(api).endpoint_in.read(bytearray(64), 80)
        self.assertLess(time.monotonic() - started, 2.0)
        cancels = [c for c in api.calls if c[0] == "cancel"]
        self.assertEqual(cancels, [("cancel", threading.get_native_id())])

    def test_a_read_that_answers_in_time_is_never_cancelled(self):
        api = FakeApi([bytes(64)])
        link(api).endpoint_in.read(bytearray(64), 1500)
        time.sleep(0.05)
        self.assertFalse([c for c in api.calls if c[0] == "cancel"])


class CloseTests(unittest.TestCase):
    def test_close_is_idempotent_and_use_afterwards_fails(self):
        api = FakeApi()
        device = link(api)
        device.close()
        device.close()
        self.assertEqual([c for c in api.calls if c[0] == "close"], [("close", 7)])
        with self.assertRaises(df.DeviceLinkError):
            device.endpoint_out.write(bytes(64), 1500)
        with self.assertRaises(df.DeviceLinkError):
            device.endpoint_in.read(bytearray(64), 1500)


if __name__ == "__main__":
    unittest.main()
