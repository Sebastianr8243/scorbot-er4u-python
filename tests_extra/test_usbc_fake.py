"""tools/usbc_fake: the fake controller's model and the guards that keep the DLL off the real USB driver."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

FOLDER = Path(__file__).resolve().parent.parent / "tools" / "usbc_fake"
sys.path.insert(0, str(FOLDER))


def load(name):
    spec = importlib.util.spec_from_file_location(name, FOLDER / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pipe_server = load("pipe_server")
fake_home = load("fake_home")
fake_jog = load("fake_jog")


def out_packet(command, mask, *, value=0, letter=0, first=1):
    packet = bytearray(64)
    packet[0], packet[4], packet[5], packet[6] = first, command, mask, letter
    packet[8:12] = value.to_bytes(4, "little", signed=True)
    return bytes(packet)


class PlantTests(unittest.TestCase):
    def test_the_roll_band_has_its_own_width(self):
        plant = pipe_server.Plant(width=2000, roll_width=300)
        self.assertEqual(plant.bands[4], (-2800, -2500))
        self.assertEqual(plant.bands[0], (-4500, -2500))
        self.assertEqual(plant.bands[1], (2500, 4500))

    def test_the_roll_bit_follows_the_mean_of_the_two_wrist_motors(self):
        plant = pipe_server.Plant(width=2000, roll_width=300)
        plant.phys[3] = plant.phys[4] = -2600
        self.assertTrue(plant.switch_bits() & 16)
        plant.phys[3] = plant.phys[4] = -2900
        self.assertFalse(plant.switch_bits() & 16)

    def test_a_drive_moves_the_axis_and_set_position_rezeroes_it(self):
        plant = pipe_server.Plant()
        plant.on_out(out_packet(0x4F, 0x01, letter=ord("H")))
        plant.on_out(out_packet(0x4D, 0x01, value=100))
        self.assertLess(plant.reported(0), 0)                  # a positive drive lowers the counts
        plant.on_out(out_packet(0x4D, 0x01, value=0))          # stop driving, then zero
        plant.on_out(out_packet(0x48, 0x01, value=0))
        self.assertEqual(plant.reported(0), 0)
        self.assertEqual(plant.events[-1]["event"], "set_position")

    def test_the_reply_echoes_the_id_and_carries_the_switch_bits(self):
        plant = pipe_server.Plant()
        plant.phys[3] = plant.phys[4] = -3000
        reply = plant.reply(out_packet(0x0D, 0, first=77))
        self.assertEqual(reply[0], 77)
        self.assertEqual(reply[5] & 16, 16)


class RateModeTests(unittest.TestCase):
    def test_rate_mode_moves_closes_stops_and_logs_a_summary(self):
        class Dll:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):        # enter_manual, emergency, move_manual, close_manual, stop
                return lambda *args: self.calls.append(name) or name != "emergency"

        class Log:
            events = []

            def event(self, name, **fields):
                self.events.append(name)
        dll, log = Dll(), Log()
        code = fake_jog.rate_action(0.02)(dll, log, None, {"busy": False, "stop_open": 0})
        self.assertEqual(code, 0)
        self.assertIn("move_manual", dll.calls)
        self.assertEqual(dll.calls[-2:], ["close_manual", "stop"])
        self.assertIn("rate_summary", log.events)


class GuardTests(unittest.TestCase):
    def ini(self, link):
        folder = tempfile.mkdtemp()
        Path(folder, "USBC.INI").write_text(f"[General]\nDriverLink  = {link}\n", encoding="latin-1")
        return folder

    def test_the_dll_is_refused_unless_its_link_is_the_pipe(self):
        usb, pipe = self.ini(r"\\.\ERUSBDevice0"), self.ini(pipe_server.PIPE)
        with self.assertRaises(SystemExit):
            fake_home.require_pipe_link(usb, host_dir=usb)
        fake_home.require_pipe_link(pipe, host_dir=pipe)

    def test_a_usbc_ini_next_to_the_host_program_must_also_name_the_pipe(self):
        good, host = self.ini(pipe_server.PIPE), self.ini(r"\\.\ERUSBDevice0")
        with self.assertRaises(SystemExit):
            fake_home.require_pipe_link(good, host_dir=host)
        fake_home.require_pipe_link(good, host_dir=self.ini(pipe_server.PIPE))
        with self.assertRaises(SystemExit):                                  # no ini there: the DLL could fall back to defaults
            fake_home.require_pipe_link(good, host_dir=tempfile.mkdtemp())
        fake_home.require_pipe_link(good, host_dir=good)                    # Python beside the DLL: one file

    def test_a_missing_link_is_refused(self):
        folder = tempfile.mkdtemp()
        Path(folder, "USBC.INI").write_text("[General]\n", encoding="latin-1")
        with self.assertRaises(SystemExit):
            fake_home.require_pipe_link(folder, host_dir=folder)


if __name__ == "__main__":
    unittest.main()
