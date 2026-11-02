"""scorbot.vendor_home: the vendor's Home as a pure state machine, checked against a recording.

``tests/fixtures/vendor_home_trace05.json`` is every command Intelitek's own ``USBC.dll`` sent while
running ``Home('A')`` against a model of the arm on a named pipe (``tests/vendor_home_plant.py`` is
that model). The port must send the same non-``0D`` commands in the same order. That proves the OUT
bytes are the vendor's; the replies were a model, and the real controller's acceptance is unverified.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from scorbot import vendor_home as vh
from tests.vendor_home_plant import Plant, commands, run, run_generator

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "vendor_home_trace05.json").read_text(encoding="utf-8"))
"""The lab DLL against the gripper-stop model: trace04 (2008 build, no gripper stops, so its gripper moves
ended on the vendor's timer) plus a ``47`` before each gripper ``4D 0``, which the vendor sends on a stall."""


def as_lists(steps):
    return [list(step) for step in steps]


class GoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outs, cls.result = run(Plant())
        cls.mine = commands(cls.outs)

    def test_every_non_nop_command_matches_the_vendor_recording(self):
        self.assertEqual(self.mine, as_lists(FIXTURE["steps"]))

    def test_the_recipe_tier_matches_too(self):
        recipe = [c for c in self.mine if (c[0] == vh.MOVE and c[3] != 0) or c[0] in (vh.SET_POSITION, vh.MODE, vh.SLAVE)]
        recipe = [c for i, c in enumerate(recipe) if i == 0 or c != recipe[i - 1]]
        self.assertEqual(recipe, as_lists(FIXTURE["recipe"]))

    def test_the_vendor_order_is_shoulder_elbow_roll_pitch_base_gripper(self):
        self.assertEqual(self.result.axes_done, (1, 2, 4, 3, 0, 5))
        self.assertEqual(self.result.axes_done, vh.HOMING_SEQUENCE)

    def test_every_axis_is_zeroed_and_the_home_ends_with_the_gripper_off(self):
        zeroed = {c[1] for c in self.mine if c[0] == vh.SET_POSITION}
        self.assertEqual(zeroed, {1 << n for n in range(6)})
        self.assertEqual(self.mine[-2:], [[vh.CONTROL_OFF, 0x20, 0, 0], [vh.MOTORS, 0x20, 0, 0]])

    def test_two_runs_send_identical_messages(self):
        again, _ = run(Plant())
        self.assertEqual([o.__dict__ for o in again], [o.__dict__ for o in self.outs])

    def test_every_motor_has_its_drive_stopped_before_it_is_zeroed_and_at_the_end(self):
        last = {}
        for out in self.outs:
            if out.cmd == vh.MOVE:
                last[out.mask] = out.arg32
            elif out.cmd == vh.SET_POSITION:
                self.assertEqual(last.get(out.mask, 0), 0, f"48 sent while mask {out.mask:#x} is still driven")
        self.assertEqual({m: v for m, v in last.items() if v}, {})

    def test_no_message_leaves_the_vendor_command_set(self):
        allowed = {vh.NOP, vh.CLEAR, vh.MODE, vh.SLAVE, vh.MOVE, vh.SET_POSITION, vh.CONTROL_OFF, vh.MOTORS}
        self.assertLessEqual({o.cmd for o in self.outs}, allowed)

    def test_setpoints_never_jump_far_from_the_measured_position(self):
        # The model's axes follow the setpoints in mode S, so a large gap would be a lurch on the real arm.
        gap = max_setpoint_gap(Plant())
        self.assertLess(gap, 1100, f"largest setpoint-to-measured gap {gap}")


def max_setpoint_gap(plant):
    """Largest |setpoint - measured count| at any message while that motor is in mode S."""
    now = 0.0
    first = vh.decode_reply(plant.exchange(vh.encode_out(vh.Out(vh.NOP), 1)), now)
    home = vh.vendor_home(first)
    out, sequence, gap = next(home), 1, 0
    while True:
        for n in range(6):
            if plant.mode[n] == "S":
                gap = max(gap, abs(out.setpoints[n] - plant.reported(n)))
        sequence = sequence % 255 + 1
        now += 0.016
        try:
            out = home.send(vh.decode_reply(plant.exchange(vh.encode_out(out, sequence)), now))
        except StopIteration:
            return gap


class CodecTests(unittest.TestCase):
    def test_out_packet_layout(self):
        packet = vh.encode_out(vh.Out(vh.MOVE, 0x02, 0x1234, -90, (1, -2, 3, 4, 5, 6)), 7)
        self.assertEqual(len(packet), 64)
        self.assertEqual(packet[0], 7)
        self.assertEqual(packet[4], 0x4D)
        self.assertEqual(packet[5], 0x02)
        self.assertEqual(packet[6:8], b"\x34\x12")
        self.assertEqual(packet[8:12], bytes.fromhex("a6ffffff"))
        self.assertEqual(int.from_bytes(packet[16:20], "little", signed=True), -2)
        self.assertEqual(packet[36:44], bytes.fromhex("010080ff010080ff"))   # the two unused slots, as the vendor writes them

    def test_the_setpoint_stream_message_is_all_nop_plus_slots(self):
        packet = vh.encode_out(vh.Out(vh.NOP, setpoints=(0,) * 6), 5)
        self.assertEqual(packet[4:12], b"\x0d" + bytes(7))

    def test_reply_counts_are_raw24_minus_7fffff(self):
        packet = bytearray(64)
        for n, count in enumerate((0, 5, -5, 70000, -70000, 1)):
            packet[19 + 5 * n:22 + 5 * n] = ((count + vh.ZERO24) & 0xFFFFFF).to_bytes(3, "little")
        packet[5], packet[59], packet[2] = 0x12, 0x20, 0x09
        reply = vh.decode_reply(bytes(packet), 1.5)
        self.assertEqual(reply.counts, (0, 5, -5, 70000, -70000, 1))
        self.assertEqual((reply.switches, reply.impact, reply.emergency, reply.now), (0x12, 0x20, True, 1.5))

    def test_emergency_bit_clear_at_the_idle_baseline(self):
        packet = bytearray(64)
        packet[2] = 0x08
        self.assertFalse(vh.decode_reply(bytes(packet), 0).emergency)

    def test_short_reply_is_refused(self):
        with self.assertRaises(ValueError):
            vh.decode_reply(bytes(40), 0)


class CouplingTests(unittest.TestCase):
    def test_drive_values_match_the_vendor_table(self):
        # The values seen in the vendor's own messages.
        self.assertEqual(vh.drive_values(vh.SHOULDER, -90), {1: -90, 2: 45, 3: 22, 4: -22})
        self.assertEqual(vh.drive_values(vh.ELBOW, 100), {2: 100, 3: 50, 4: -50})
        self.assertEqual(vh.drive_values(vh.WRIST_M1, 90), {3: 90, 4: -90})
        self.assertEqual(vh.drive_values(vh.WRIST_M2, -90), {4: -90, 3: -90})
        self.assertEqual(vh.drive_values(vh.BASE, 110), {0: 110})

    def test_position_vectors_match_the_offset_table(self):
        self.assertEqual(vh.position_vector(vh.SHOULDER, -190), {1: -190, 2: 190, 3: 95, 4: -95})
        self.assertEqual(vh.position_vector(vh.ELBOW, 45), {2: 45, 3: 22, 4: -22})
        self.assertEqual(vh.position_vector(vh.WRIST_M1, 850), {3: 850, 4: -850})
        self.assertEqual(vh.position_vector(vh.WRIST_M2, -690), {4: -690, 3: -690})
        self.assertEqual(vh.position_vector(vh.WRIST_M1, vh.PREPASS_PITCH_COUNTS), {3: 1050, 4: -1050})

    def test_the_config_matches_the_lab_arms_parameter_files(self):
        self.assertEqual([(a.type, a.velocity, a.offset) for a in vh.AXES],
                         [(14, 110, 0), (16, 90, -190), (14, 100, 45), (15, 90, 850), (16, -90, -690), (17, -165, 0)])


class SafetyTests(unittest.TestCase):
    def test_emergency_input_raises_300_at_once(self):
        plant = Plant()
        with self.assertRaises(vh.HomeError) as caught:
            run(plant, on_cycle=lambda n, p: setattr(p, "emergency", n > 50))
        self.assertEqual(caught.exception.code, vh.ERR_EMERGENCY)

    def test_an_impact_flag_during_a_planner_move_raises_201(self):
        plant = Plant()
        plant.mode[vh.SHOULDER] = "S"
        with self.assertRaises(vh.HomeError) as caught:
            run_generator(lambda first: vh._Run(first, 0).ramp({vh.SHOULDER: 100}, vh.SHOULDER), plant,
                          on_cycle=lambda n, p: setattr(p, "impact", 0x02 if n > 3 else 0))
        self.assertEqual(caught.exception.code, vh.ERR_IMPACT)

    def test_an_impact_during_a_type_14_search_reverses_like_a_stall_and_never_zeroes_unfound(self):
        # A hard stop during the elbow's forward pass is the vendor's cue to reverse; a later phase that
        # then cannot find its switch is a fault, not a zero.
        with self.assertRaises(vh.HomeError) as caught:
            run(Plant(), on_cycle=lambda n, p: setattr(p, "impact", 0x04 if n > 900 else 0))
        self.assertIn(caught.exception.code, (vh.ERR_NOT_FOUND, vh.ERR_IMPACT))

    def test_a_switch_that_never_trips_ends_in_a_timeout(self):
        with self.assertRaises(vh.HomeError) as caught:
            run(Plant(bands={}), cycle_s=0.05)
        self.assertEqual(caught.exception.code, vh.ERR_TIMEOUT)

    def test_an_axis_that_does_not_move_ends_on_the_vendors_time_limit(self):
        # The shoulder searches first and is type 16: the vendor has no stall check there, only MaxTime.
        with self.assertRaises(vh.HomeError) as caught:
            run(Plant(gain=0.0), cycle_s=0.1, max_cycles=5000)
        self.assertEqual((caught.exception.code, caught.exception.axis), (vh.ERR_TIMEOUT, vh.SHOULDER))
        self.assertNotIn(vh.SET_POSITION, [o.cmd for o in caught.exception.outs])

    def test_a_missing_shoulder_switch_ends_on_the_time_limit_and_zeroes_nothing(self):
        with self.assertRaises(vh.HomeError) as caught:
            run(Plant(bands={**Plant().bands, 1: (50000, 60000)}), cycle_s=0.1)
        self.assertEqual((caught.exception.code, caught.exception.axis), (vh.ERR_TIMEOUT, vh.SHOULDER))
        self.assertNotIn(vh.SET_POSITION, [o.cmd for o in caught.exception.outs])

    def test_a_jammed_wrist_motor_never_lets_the_home_finish(self):
        with self.assertRaises(vh.HomeError) as caught:
            run(Plant(frozen={vh.WRIST_M1}))
        self.assertEqual(caught.exception.code, vh.ERR_TIMEOUT)

    def test_a_wrong_drive_sign_stops_the_home_within_a_few_degrees(self):
        plant = Plant(gain=-0.1)                                   # a positive 4D RAISES the counts
        with self.assertRaises(vh.HomeError) as caught:
            run(plant, max_cycles=5000)
        self.assertEqual(caught.exception.code, vh.ERR_DIRECTION)
        self.assertLess(max(abs(x) for x in plant.phys[:5]), 600)  # far less than one switch band (the gripper starts at 3000)

    def test_a_controller_that_answers_late_does_not_look_like_a_stall(self):
        # The controller may run about 47 messages behind (docs/protocol/BRING_UP_SEQUENCE.md): after a drive
        # command the replies can show no motion for that long. A healthy arm must not be stopped for it.
        def late(n, plant):
            plant.frozen = set(range(6)) if n < 60 else set()
        outs, result = run(Plant(), on_cycle=late)
        self.assertEqual(result.axes_done, vh.HOMING_SEQUENCE)

    def test_a_motor_that_starts_late_in_a_must_find_search_is_not_a_missed_switch(self):
        # The wrist's reach phases used the vendor's 2-cycle impact tick with a stall limit of 1: an arm that
        # took 8 cycles to answer a new wrist drive (every motor lags together, the commands go out one
        # message apart) ended the home with "switch not found".
        state = {"drive": None, "since": 0}

        def sluggish_wrist(n, plant):
            if plant.drive[vh.WRIST_M1] != state["drive"]:
                state["drive"], state["since"] = plant.drive[vh.WRIST_M1], n
            late = state["drive"] and n - state["since"] < 8
            plant.frozen = set(range(6)) if late else set()
        outs, result = run(Plant(), on_cycle=sluggish_wrist)
        self.assertEqual(result.axes_done, vh.HOMING_SEQUENCE)
        self.assertEqual(commands(outs), [list(s) for s in FIXTURE["steps"]])

    def test_a_motor_that_runs_away_while_settling_ends_on_the_time_limit(self):
        plant = Plant(frozen={vh.SHOULDER})
        plant.mode[vh.SHOULDER] = "S"

        def runaway(n, p):
            if n > 20:
                p.phys[vh.SHOULDER] -= 21
        with self.assertRaises(vh.HomeError) as caught:
            run_generator(lambda first: vh._Run(first, 0).ramp({vh.SHOULDER: 100}, vh.SHOULDER), plant,
                          on_cycle=runaway, cycle_s=0.1)
        self.assertEqual((caught.exception.code, caught.exception.axis), (vh.ERR_TIMEOUT, vh.SHOULDER))

    def test_the_credit_for_a_slow_controller_is_bounded_by_what_the_home_will_accept(self):
        # A stuck motor must not be allowed most of the 1050-count pre-pass move because of an unverified lag.
        self.assertLessEqual(vh.MAX_LAG, 60)

    def test_every_axis_uses_the_vendors_own_distance_and_time(self):
        self.assertEqual([a.max_distance for a in vh.AXES], [99999, 30000, 10000, 10000, 10000, 10000])
        self.assertEqual([a.max_time_s for a in vh.AXES], [110.0, 60.0, 90.0, 55.0, 75.0, 15.0])

    def test_an_impact_during_a_type_16_search_is_a_fault(self):
        with self.assertRaises(vh.HomeError) as caught:
            run(Plant(), on_cycle=lambda n, p: setattr(p, "impact", 0x02 if 60 < n < 70 else 0))
        self.assertEqual(caught.exception.code, vh.ERR_IMPACT)

    def test_a_move_that_never_settles_ends_on_the_time_limit(self):
        plant = Plant()
        plant.mode[vh.SHOULDER] = "S"
        plant.frozen = {vh.SHOULDER}
        with self.assertRaises(vh.HomeError) as caught:
            run_generator(lambda first: vh._Run(first, 0).ramp({vh.SHOULDER: 100}, vh.SHOULDER), plant, cycle_s=0.1)
        self.assertEqual((caught.exception.code, caught.exception.axis), (vh.ERR_TIMEOUT, vh.SHOULDER))

    def test_a_creep_that_never_finds_the_edge_ends_on_the_time_limit(self):
        plant = Plant(bands={}, frozen={vh.SHOULDER})
        plant.mode[vh.SHOULDER] = "S"
        with self.assertRaises(vh.HomeError) as caught:
            run_generator(lambda first: vh._Run(first, 0).fine(vh.SHOULDER), plant, cycle_s=0.1)
        self.assertEqual(caught.exception.code, vh.ERR_TIMEOUT)

    def test_a_gripper_that_does_not_move_ends_its_drives_like_the_vendors(self):
        # The vendor has no minimum travel: a gripper that cannot move stalls at once and is zeroed where it is.
        # The lab-day ALIGNED check, not the home, is what catches that.
        outs, result = run(Plant(frozen={vh.GRIPPER}))
        self.assertEqual(result.axes_done, vh.HOMING_SEQUENCE)

    def test_a_gripper_impact_ends_the_drive_normally(self):
        outs, _ = run_generator(lambda first: vh._Run(first, 0).gripper_drive(-165), Plant(),
                                on_cycle=lambda n, p: setattr(p, "impact", 0x20))
        self.assertEqual(outs[-1].cmd, vh.MOTORS)

    def test_a_gripper_stall_ends_like_the_vendor_clear_first_and_within_a_few_cycles(self):
        # The lab DLL checks every ImpactCondTicks (2) cycles for at most ImpactCondEnc (2) counts of motion,
        # then sends 47 before 4D 0 (trace05; docs/protocol/LEDGER.md). Half open to the open stop is 3000 counts.
        outs, _ = run_generator(lambda first: vh._Run(first, 0).gripper_drive(-165), Plant())
        cmds = [o.cmd for o in outs]
        drive, stop = cmds.index(vh.MOVE), len(cmds) - 1 - cmds[::-1].index(vh.MOVE)
        self.assertEqual(cmds[stop - 1], vh.CLEAR)
        stroke = -(-3000 // int(165 * 0.5))
        self.assertLessEqual(stop - drive, stroke + 8)

    def test_a_late_controller_does_not_make_a_gripper_look_stalled(self):
        plant = Plant()

        def late(n, p):
            p.frozen = {vh.GRIPPER} if n < 45 else set()     # the replies show no motion for the first 40 cycles
        run_generator(lambda first: vh._Run(first, 0).gripper_drive(-165), plant, on_cycle=late, lag=40)
        self.assertEqual(plant.reported(vh.GRIPPER), 3000)                   # half open to the open stop

    def test_repeated_replies_mid_travel_are_not_a_gripper_stall(self):
        # A controller that sends the same reply twice (same echoed ID, same counts) after the gripper has moved
        # far enough must not end the drive and zero the gripper half way.
        class Repeats(Plant):
            n, last = 0, None

            def exchange(self, data):
                self.n += 1
                fresh = super().exchange(data)
                if self.last is not None and self.n in (19, 20, 21):
                    return self.last
                self.last = fresh
                return fresh
        plant = Repeats()
        run_generator(lambda first: vh._Run(first, 0).gripper_drive(-165), plant)
        self.assertEqual(plant.reported(vh.GRIPPER), 3000)                   # it reached the open stop

    def test_one_quiet_check_ends_the_gripper_drive_as_the_vendors_does(self):
        frozen = set(range(20, 23))                  # 3 fresh replies standing still: one whole quiet 2-reply check
        plant = Plant()
        run_generator(lambda first: vh._Run(first, 0).gripper_drive(-165), plant,
                      on_cycle=lambda n, p: setattr(p, "frozen", {vh.GRIPPER} if n in frozen else set()))
        self.assertLess(plant.reported(vh.GRIPPER), 3000)
        self.assertEqual(vh.GRIPPER_STALL_CHECKS, 1)

    def test_a_gripper_that_keeps_moving_ends_on_the_vendors_budget(self):
        with self.assertRaises(vh.HomeError) as caught:
            run_generator(lambda first: vh._Run(first, 0).gripper_drive(-165), Plant(gripper_range=(-10**6, 10**6)))
        self.assertEqual(caught.exception.code, vh.ERR_GRIPPER)

    def test_a_gripper_that_jitters_at_its_stop_is_stopped_at_the_vendors_budget(self):
        # Feedback that moves 3 counts every reply never meets the 2-count stall rule. The vendor stops after
        # 120 replies anyway; here that is ERR_GRIPPER, raised before any 48.
        def jitter(n, p):
            p.off[vh.GRIPPER] = -3000 + (3 if n // 2 % 2 else 0)            # 2-reply samples always 3 apart
        with self.assertRaises(vh.HomeError) as caught:
            run_generator(lambda first: vh._Run(first, 0).gripper_drive(-165), Plant(), on_cycle=jitter)
        self.assertEqual(caught.exception.code, vh.ERR_GRIPPER)
        outs = caught.exception.outs
        drive = next(i for i, o in enumerate(outs) if o.cmd == vh.MOVE and o.arg32)
        self.assertLessEqual(len(outs) - drive, vh.GRIPPER_MAX_CYCLES + 12)  # the budget plus the stop messages
        self.assertNotIn(vh.SET_POSITION, [o.cmd for o in outs])

    def test_the_reply_carries_the_echoed_id(self):
        packet = bytearray(64)
        packet[0] = 77
        self.assertEqual(vh.decode_reply(bytes(packet), 0.0).echo, 77)

    def test_a_switch_that_is_not_found_in_a_later_phase_is_a_fault_not_a_zero(self):
        # No elbow band at all: the first forward pass runs into its distance bound and reverses,
        # then the phase that must find the switch cannot.
        bands = {k: v for k, v in Plant().bands.items() if k != vh.ELBOW}
        with self.assertRaises(vh.HomeError) as caught:
            run(Plant(bands=bands))
        self.assertIn(caught.exception.code, (vh.ERR_NOT_FOUND, vh.ERR_TIMEOUT, vh.ERR_TRAVEL, vh.ERR_STALL))
        zeroings = [o for o in caught.exception.outs if o.cmd == vh.SET_POSITION and o.mask == 1 << vh.ELBOW]
        self.assertEqual(len(zeroings), 1)          # only the shoulder's tail zeroed it, never the elbow's own

    def test_every_error_first_sends_the_stop_then_raises(self):
        plant = Plant()
        with self.assertRaises(vh.HomeError) as caught:
            run(plant, on_cycle=lambda n, p: setattr(p, "emergency", n > 50))
        outs = caught.exception.outs
        tail = outs[-10:]
        self.assertEqual([o.cmd for o in tail], [vh.CLEAR] + [vh.MOVE] * 6 + [vh.NOP, vh.NOP, vh.MODE])
        self.assertEqual([o.mask for o in tail[1:7]], [1 << n for n in range(6)])
        self.assertTrue(all(o.arg32 == 0 for o in tail[1:7]))
        self.assertEqual((tail[-1].mask, tail[-1].arg16), (0xFF, vh.MODE_S))

    def test_the_stop_after_an_error_holds_the_setpoints_at_the_measured_position(self):
        plant = Plant()
        with self.assertRaises(vh.HomeError) as caught:
            run(plant, on_cycle=lambda n, p: setattr(p, "emergency", n > 50))
        final = caught.exception.outs[-1].setpoints
        self.assertEqual(list(final), [plant.reported(n) for n in range(6)])

    def test_the_motors_are_never_commanded_after_an_error_because_the_generator_stops(self):
        plant = Plant()
        sent = []

        def watch(n, p):
            p.emergency = n > 50
            sent.append(n)
        with self.assertRaises(vh.HomeError):
            run(plant, on_cycle=watch)
        self.assertLess(len(sent), 200)


class VariantTests(unittest.TestCase):
    def test_single_axis_runs_skip_the_prepass_and_the_sweep(self):
        outs, result = run(Plant(), axes=(vh.SHOULDER,))
        self.assertEqual(result.axes_done, (vh.SHOULDER,))
        sent = commands(outs)
        self.assertEqual(sent[0], [vh.MODE, 0x04, vh.MODE_S, 0])          # slave pairs first, coupled motors before the axis
        self.assertNotIn([vh.MOTORS, 0x20, 1, 0], sent)                   # the gripper was not touched
        self.assertEqual(sum(1 for c in sent if c[0] == vh.SET_POSITION), 4)   # elbow, m1, m2, shoulder

    def test_a_newer_controller_version_skips_the_extra_mode_messages_and_clears_after_each_48(self):
        old, _ = run(Plant(), version=0)
        new, _ = run(Plant(), version=9)
        self.assertGreater(sum(1 for o in old if o.cmd == vh.MODE and o.arg16 == vh.MODE_T),
                           sum(1 for o in new if o.cmd == vh.MODE and o.arg16 == vh.MODE_T))
        sent = commands(new)
        first_48 = next(i for i, c in enumerate(sent) if c[0] == vh.SET_POSITION)
        self.assertEqual(sent[first_48 + 1], [vh.CLEAR, 0, 0, 0])

    def test_starting_on_the_shoulder_switch_drives_off_it_first(self):
        # Shoulder switch band covers the start: the type 16 search must go the other way first.
        plant = Plant(bands={**Plant().bands, 1: (-100, 4500)})
        outs, _ = run(plant)
        first_moves = [c for c in commands(outs) if c[0] == vh.MOVE and c[3] != 0][:2]
        self.assertEqual(first_moves[0], [vh.MOVE, 0x04, 0, -45])          # coupled elbow first, then the shoulder (+V)


class ModuleHygieneTests(unittest.TestCase):
    def test_importing_the_module_loads_no_usb_and_no_legacy_code(self):
        code = ("import sys; import scorbot.vendor_home; "
                "bad = [m for m in sys.modules if m.split('.')[0] in ('usb', 'libdef', 'libcomm', 'numpy')]; "
                "print(bad)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                             cwd=Path(__file__).resolve().parent.parent).stdout.strip()
        self.assertEqual(out, "[]")


if __name__ == "__main__":
    unittest.main()
