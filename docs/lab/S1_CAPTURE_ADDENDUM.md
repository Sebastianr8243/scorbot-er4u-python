# S1 addendum: captures for the questions the desk work left open

Written 2026-10-07. Do these in the same session as the [S1 card](S1_CAPTURE_LAB_CARD.md), with the same setup (Intelitek driver back, USBPcap on, one program connected, someone at the physical stop, the arm near home). They are SCORBASE captures only: nothing here runs our code on the arm. Analysis is offline, as in [USB_CAPTURE.md](USB_CAPTURE.md).

## What the desk work already settled (2026-10-08), so these captures are optional

Before taking any of these, note that reading the DLL and doing the arithmetic answers most of them. In order of how much the capture would still add:

| Question | Answer from the desk | Tag | What a capture would add |
|---|---|---|---|
| **J.** Is Go Home one controller command? | **No.** None of the DLL's exported functions is a Go Home (the list was checked in the 2018 build). At the end of `Home` it stores the position named `0` (`Here`, [VENDOR_HOMING_TRACE.md](../protocol/VENDOR_HOMING_TRACE.md) step 6), and all motion in this DLL is a PC-streamed `0D` setpoint move ([VENDOR_DLL_PROTOCOL.md](../protocol/VENDOR_DLL_PROTOCOL.md)). So Go Home is an ordinary move to stored point 0. BACKLOG 38's "if it is a single command" is answered: it is not. `park_at_home` is the right shape. | V for the exports, I for how SCORBASE calls it | Only the speed profile of the move |
| **K.** What does `SetHome` do? | The code ORs the homed mask and sends `48` value 0 per axis. `Home` itself uses `48` right after the switch edge to zero the counter at that point, so `48` is "zero the counter here". `PROTOCOL.md` line 381 ("only stores a position, does not mark homed") is contradicted by the code. | V for the calls, I for the effect, high confidence | Confirms `48`'s payload; needed before it goes into our code (the project rule) |
| **L.** Does the vendor home tolerate a slightly-off start? | **Yes, by design.** Each homing type handles a start on the switch (drive off first), and `home_inch` and its tests follow the trace ([VENDOR_HOMING_TRACE.md](../protocol/VENDOR_HOMING_TRACE.md) section 3 and the table at section 7). The limit is the per-axis `MaxDistance` in the summary table at the top of that trace, not a rule that the start must be exact. | V | The axis order on this controller |
| **I.** Does SCORBASE's joint jog move one motor? | The only manual-move API in the DLL (`MoveManual`) drives one motor (V). What the SCORBASE front end sends is not in the DLL. | V for the API | The front end's behavior, for the wrist buttons especially |

**The wrist sign is a smaller risk than earlier notes said.** Two reasons, both checkable:

1. *Structure agrees from two independent sources.* The legacy jog table (written by the OpenScorbot authors for a real arm) has pitch as wrist motors 1 and 2 moving **opposite** ways (orders 10 and 11) and roll as both the **same** way (12 and 13). The vendor DLL's own conversion has the same structure: pitch depends on `(e3 - e4) / 2`, roll on `(e3 + e4) / 2`. The scales differ (legacy 33.8 counts per degree of pitch, vendor 27.9), but our coupled moves work in the vendor's counts and ask the legacy plan for the matching jog, so the scale difference cancels.
2. *A wrong sign is caught by the readback, and it is cheap.* Every jog is read back and the next step is checked. `tests/test_coupled_moves.py::test_a_wrist_that_physically_moves_the_wrong_way_is_caught_within_a_few_degrees` flips the simulated wrist's physical response: the move faults after two wrist jogs, with about 4 degrees of wrist pitch swing, no roll, motors off. That bound holds only if the real encoders report truthfully, which is what the first supervised trial still checks.

What thinking cannot settle: whether the real order table and the encoders behave like the model, switch polarity, and the hard stops. Those are the lab's.

## The captures, if you want them

Why these four: the desk traces say what the DLL would send. They cannot say what SCORBASE's front end asks of the DLL, or what the controller does with it. Capture I is the most useful of the four because it shows the front end's wrist behavior from the vendor's own traffic.

| Save as | Steps in SCORBASE | Write down | Settles |
|---|---|---|---|
| `intelitek_I_prehome_joint.pcapng` | Fresh start: connect, **Control On**, do **not** home. Open Manual Movement in **Joint mode**. Jog **shoulder** about 1 second (small), release, wait 3 s. **Elbow** about 1 second, release, wait 3 s. **Base** about 1 second, release, wait 3 s. Close | Which direction each key moved the arm; what the forearm and gripper did while only the shoulder key was held (did the gripper keep pointing the same way in space?); any limit message | Whether SCORBASE's joint jog moves **one motor** (the DLL says so) or the coupled motors too; the model's prediction that the forearm and gripper keep their direction in space |
| `intelitek_J_gohome.pcapng` | Home. Jog two joints a few degrees (as in capture B). Then **Go Home**. Close | Whether the arm moved in one smooth move or axis by axis; how long it took; where it ended against the home you recorded | Whether Go Home is one controller command (BACKLOG 38) or a stream of setpoints, and what it does with the coupled motors |
| `intelitek_K_sethome.pcapng` | **Only if the menus have a Set Home or Define Home item** (the DLL exports `SetHome`; the front end may not use it). Home, jog the base a few degrees, run it, then read the position SCORBASE shows. If there is no such item, skip this capture and write that down | The exact menu path and the position shown before and after | Whether `SetHome` zeroes the counters with `48` and marks the arm homed (`PROTOCOL.md` says it does not mark homed; the code read says it does) |
| `intelitek_L_home_from_off.pcapng` | Place the arm **a few degrees away from its home pose by hand** with Control Off, then Control On and **Home**. Close | The pose before; what moved during homing and in what order; any error | Whether the vendor home tolerates an off-home start, and in which order the axes and the coupled motors move (the trace says shoulder, elbow, wrist, base) |

Keep every move small and away from the table and cables. Stop at the first surprise and keep what you have.

## What to look for at the desk

Export and compare as in [USB_CAPTURE.md section 4](USB_CAPTURE.md#4-find-the-device-export-compare). Start with capture I, because it answers the question that decided the pre-home design:

1. Find the packets sent while a key was held. In each, see **which of the five motor setpoint or velocity slots change**. If holding SHOULDER only changes the shoulder slot, the front end drives one motor and the DLL trace is the whole story. If the elbow and wrist slots change too, SCORBASE couples them and our default single-motor move is *less* than the vendor's.
2. Compare the sizes of the changes with the signs in [VENDOR_COUPLING_TRACE.md](../protocol/VENDOR_COUPLING_TRACE.md). A wrist change that is **opposite in sign** or **twice** the predicted size is the wrong-sign result the first trial is meant to catch, found without moving the arm ourselves.
3. In `J`, count the packets between the first and last setpoint change and note the commanded values. One message with a target means a controller-side move; a ramp of setpoints means the PC drives it.
4. In `K` and `L`, look for a `48` message (set position, value 0). Write its payload down; a byte the legacy code never sends is what the project rule says needs a capture before it goes into our code.
5. In the connect or homing packets of any capture, find the reply to `72` (controller version, reply bytes 11-14); it decides the version-8 branches in the homing trace. Record the number.

## After the visit

Record each verdict in `docs/protocol/VENDOR_MANUAL_MOVE_TRACE.md` and `VENDOR_HOMING_TRACE.md` as V (seen on the wire) or contradicted, correct `PROTOCOL.md` line 381 about `SetHome`, and tell the owner before changing any packet code. Nothing in `openScorbot/` changes without these captures.
