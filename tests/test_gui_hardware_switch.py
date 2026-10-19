"""The legacy GUI's connect honours the hardware switch. gui.py is loaded with every legacy, USB and Qt
module replaced by a fake, so nothing real is imported, no data.json is written, no USB is touched."""

import importlib.util
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

GUI = Path(__file__).resolve().parent.parent / "openScorbot" / "gui.py"


def load_gui():
    usb = types.ModuleType("usb")
    usb.core = mock.Mock(USBError=type("USBError", (Exception,), {}))
    usb.util = mock.Mock()
    qt = types.ModuleType("PyQt5")
    qt.QtWidgets = types.SimpleNamespace(QMainWindow=object, QApplication=mock.Mock())
    qt.uic, qt.QtGui = mock.Mock(), mock.Mock()
    fakes = {"usb": usb, "usb.core": usb.core, "usb.util": usb.util, "PyQt5": qt,
             **{name: mock.Mock() for name in ("libsync", "libcomm", "libdef", "log", "conf")}}
    with mock.patch.dict(sys.modules, fakes):
        spec = importlib.util.spec_from_file_location("legacy_gui_under_test", GUI)
        gui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gui)
    return gui, usb.core


class GuiHardwareSwitchTests(unittest.TestCase):
    def connect_with(self, env):
        gui, core = load_gui()
        core.find.return_value = None       # switch on: behave as "device not found", nothing further
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out):
            gui.set_conection()
        return gui, core, out.getvalue()

    def test_switch_off_never_looks_up_resets_or_claims_the_device(self):
        for env in ({}, {"SCORBOT_HARDWARE": "0"}, {"SCORBOT_HARDWARE": "yes"}, {"SCORBOT_HARDWARE": ""}):
            with self.subTest(env=env):
                gui, core, _ = self.connect_with(env)
                core.find.assert_not_called()
                self.assertFalse(gui.online)

    def test_switch_off_message_names_the_switch(self):
        _, _, printed = self.connect_with({})
        self.assertIn("SCORBOT_HARDWARE", printed)

    def test_switch_on_looks_up_the_device_as_before(self):
        gui, core, printed = self.connect_with({"SCORBOT_HARDWARE": "1"})
        core.find.assert_called_once_with(idVendor=0x09F1, idProduct=0x0007)
        self.assertIn("Device not found", printed)
        self.assertFalse(gui.online)


if __name__ == "__main__":
    unittest.main()
