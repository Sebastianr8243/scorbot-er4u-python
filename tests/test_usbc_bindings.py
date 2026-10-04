"""tools/usbc_probe/bindings.py against a fake library. The real USBC.dll is never loaded."""

import ctypes
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BINDINGS = Path(__file__).resolve().parent.parent / "tools" / "usbc_probe" / "bindings.py"
spec = importlib.util.spec_from_file_location("usbc_bindings", BINDINGS)
vendor_dll = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = vendor_dll  # dataclasses looks the module up by name
spec.loader.exec_module(vendor_dll)
Export, UsbcDll, VendorDllError = vendor_dll.Export, vendor_dll.UsbcDll, vendor_dll.VendorDllError

SYMBOL = "?GetParameterFolder@@YAHPAD@Z"
FOLDER = b"C:\\Intelitek\\PAR\\ER4u"


class FakeFunction:
    def __init__(self, result=1, write=None):
        self.result = result
        self.write = write
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if self.write is not None:
            args[0].value = self.write
        return self.result


class FakeLibrary:
    def __init__(self, **functions):
        for symbol, function in functions.items():
            setattr(self, symbol, function)


MOVE = Export(
    name="MoveManual",
    symbol="?Fake@@YAHH@Z",
    restype=ctypes.c_int,
    argtypes=(ctypes.c_int,),
    energises=True,
)


class VendorDllTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".dll")
        os.close(handle)
        self.addCleanup(os.remove, self.path)
        patcher = mock.patch.object(vendor_dll, "_pointer_bits", return_value=32)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, library, **kwargs):
        return UsbcDll(self.path, loader=lambda path: library, **kwargs)

    def test_64_bit_python_is_refused_before_loading(self):
        loads = []
        with mock.patch.object(vendor_dll, "_pointer_bits", return_value=64):
            with self.assertRaisesRegex(VendorDllError, "64-bit"):
                UsbcDll(self.path, loader=loads.append)
        self.assertEqual(loads, [])

    def test_missing_file_is_refused_before_loading(self):
        loads = []
        with self.assertRaisesRegex(VendorDllError, "no DLL at"):
            UsbcDll(self.path + ".missing", loader=loads.append)
        self.assertEqual(loads, [])

    def test_loader_gets_an_absolute_path(self):
        loads = []

        def loader(path):
            loads.append(path)
            return FakeLibrary(**{SYMBOL: FakeFunction()})

        UsbcDll(os.path.relpath(self.path), loader=loader)
        self.assertEqual(loads, [os.path.abspath(self.path)])

    def test_load_failure_reports_the_os_error(self):
        def loader(path):
            error = OSError("not a valid Win32 application")
            error.winerror = 193
            raise error

        with self.assertRaisesRegex(VendorDllError, "OS error 193"):
            UsbcDll(self.path, loader=loader)

    def test_missing_export_names_the_symbol(self):
        with self.assertRaises(VendorDllError) as caught:
            self.make(FakeLibrary())
        self.assertIn(SYMBOL, str(caught.exception))

    def test_binding_pins_argtypes_and_restype(self):
        function = FakeFunction()
        self.make(FakeLibrary(**{SYMBOL: function}))
        self.assertEqual(function.argtypes, [ctypes.c_char_p])
        self.assertIs(function.restype, ctypes.c_int)

    def test_get_parameter_folder_returns_code_and_text(self):
        function = FakeFunction(result=7, write=FOLDER)
        dll = self.make(FakeLibrary(**{SYMBOL: function}))
        self.assertEqual(dll.get_parameter_folder(), (7, FOLDER.decode("ascii")))
        (buffer,) = function.calls[0]
        self.assertGreater(ctypes.sizeof(buffer), 260)

    def test_longest_path_that_fits_is_accepted(self):
        function = FakeFunction(write=b"x" * 259)
        dll = self.make(FakeLibrary(**{SYMBOL: function}))
        self.assertEqual(dll.get_parameter_folder(), (1, "x" * 259))

    def test_write_past_the_buffer_size_is_reported(self):
        function = FakeFunction(write=b"x" * 260)
        dll = self.make(FakeLibrary(**{SYMBOL: function}))
        with self.assertRaisesRegex(VendorDllError, "more than 260 bytes"):
            dll.get_parameter_folder()

    def test_buffer_smaller_than_max_path_is_rejected_without_a_call(self):
        function = FakeFunction()
        dll = self.make(FakeLibrary(**{SYMBOL: function}))
        with self.assertRaises(ValueError):
            dll.get_parameter_folder(buffer_size=16)
        self.assertEqual(function.calls, [])

    def test_energising_export_is_refused_without_calling_the_dll(self):
        function = FakeFunction()
        dll = self.make(FakeLibrary(**{MOVE.symbol: function}), exports=(MOVE,))
        with self.assertRaisesRegex(VendorDllError, "supervised_mode is False"):
            dll._call("MoveManual", 0)
        self.assertEqual(function.calls, [])

    def test_energising_export_is_called_in_supervised_mode(self):
        function = FakeFunction(result=1)
        dll = self.make(
            FakeLibrary(**{MOVE.symbol: function}), supervised_mode=True, exports=(MOVE,)
        )
        self.assertEqual(dll._call("MoveManual", 0), 1)
        self.assertEqual(function.calls, [(0,)])

    def test_supervised_mode_must_be_a_bool(self):
        with self.assertRaises(TypeError):
            self.make(FakeLibrary(), supervised_mode=1)

    def test_unknown_binding_name_is_an_error(self):
        dll = self.make(FakeLibrary(**{SYMBOL: FakeFunction()}))
        with self.assertRaisesRegex(VendorDllError, "no binding named"):
            dll._call("Nope")

    def test_shipped_exports_are_getters_only(self):
        self.assertEqual([e.name for e in vendor_dll.EXPORTS], ["GetParameterFolder"])
        self.assertFalse(any(e.energises for e in vendor_dll.EXPORTS))


class ImportTest(unittest.TestCase):
    def test_bindings_do_not_import_the_sdk_or_usb(self):
        source = BINDINGS.read_text(encoding="utf-8")
        self.assertNotIn("import scorbot", source)
        self.assertNotIn("from scorbot", source)
        self.assertNotIn("import usb", source)


if __name__ == "__main__":
    unittest.main()
