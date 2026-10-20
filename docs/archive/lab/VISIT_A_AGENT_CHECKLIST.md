# Visit A checklist: record the vendor stack on the real arm

Written 2026-10-12. For an AI agent on the lab PC that helps a human operator. **Nothing here has been run on the arm.** Read [AGENT_RUNBOOK.md](AGENT_RUNBOOK.md) first; its agent rules apply to every line below. Plan: [first-motion plan](../specs/2026-10-11-first-motion-plan.md), Phase 1. Capture mechanics: [S1_CAPTURE_LAB_CARD.md](S1_CAPTURE_LAB_CARD.md).

## What this visit is for

No code of ours moves the arm. We only **watch** the vendor's own software (SCORBASE, then `usbc_home`) drive the real controller, with USBPcap recording every packet. That recording is the only way to learn four things about the real controller:

| Question | Which capture answers it |
|---|---|
| 1. Does the controller accept the home commands (`4D`, `48`, `4F`)? | SCORBASE Home (step 5) |
| 2. How late does a stop land? | F9 stop during a move (step 6) |
| 3. Which way does each joint turn, and how many counts per degree? | Hand sweep (step 3) and the 1 degree jog (step 5) |
| 4. Does the collision flag (IN byte 59) stop a drive? | Only if one shows up in the Home capture; no need to provoke it |

Plus one check before any vendor home: the **wrist roll switch should be at least about 800 counts wide** (the vendor's roll search has no distance limit; see `docs/archive/specs/2026-10-08-vendor-home-port.md`, "Desk results of 2026-10-12").

## Ground rules

- **The operator drives the arm.** They run the SCORBASE and `usbc_home` commands and type any confirmation word. The agent handles the capture, the notes and the no-arm checks.
- **Keep our own motion code off the controller until step 9** (`scorbot`, `examples\`, the bench scripts). Our connect resets it and turns the motors on, which would spoil the vendor recording. `usbc_home` is different: it only calls Intelitek's own DLL, so it belongs to the vendor block.
- **If anything looks wrong** (unexpected motion, the MOTORS LED not matching, a joint near a hard stop, an error dialog), stop, keep the logs and tell the operator. The physical stop is the real emergency stop; F9 and `Stop` are not.
- **Don't run firmware or update tools, and don't commit Intelitek files or captures.** They stay in `logs\` or a scratch folder.

## Before leaving home (desk)

- [ ] `python -m pytest -q tests tests_extra` passes. Write the counts down.
- [ ] On a USB stick: the repo (`git pull` the branch, or a ZIP), the 32-bit embeddable Python from `C:\Users\sebas\tools\py32`, and this card.
- [ ] Wireshark with USBPcap installer, if the lab PC lacks it (needs admin and a reboot).
- [ ] Phone with a level app, a pen and paper, a screwdriver with a small tip (microswitch press, optional).

## At the lab PC

### 0. Save what is there (arm unpowered or idle, nothing connected)

- [ ] Copy the whole existing `logs\` folder to the stick **first**.
- [ ] Copy the SCORBASE `BIN\` folder (`USBC.dll`, `USBC.INI`, `ER4CONF.INI`, `Par\`) to a scratch folder outside the repo, e.g. `C:\scratch\bin` (called `<bin>` below). Copy the lab's `USBC.dll` and the inis to the stick too (never commit). Write down the SCORBASE version from Help > About.
- [ ] Search that folder for `usbc.h`, `usbcdef.h`, `error.h`, `usbc.lib`; note any found.
- [ ] Photograph Device Manager > the controller > Driver > Provider (expect Intelitek's `ERUSBClass`).
- [ ] Photograph the controller's POWER and MOTORS LEDs at power-on.
- [ ] Optional, only with the owner's OK and the controller **unplugged**: photograph the board inside, chip markings in focus, without touching anything.

### 1. No-arm checks of the tool (arm unpowered)

- [ ] Copy the **whole** 32-bit embeddable Python folder from the stick (`python.exe`, `python313.dll`, the `.pyd` files, the zip) into `<bin>`, next to `USBC.dll`.
- [ ] `<bin>\python.exe tools\usbc_home\usbc_home.py --dll-dir <bin> --probe`: write down the SHA-256. It is a known build if it matches `KNOWN_DLL_SHA256` in `tools\usbc_home\usbc_home.py`; otherwise record it as a **third build** (then `--allow-unknown-dll` is a deliberate choice later, not a default).
- [ ] `<bin>\python.exe tools\usbc_home\usbc_home.py --dll-dir <bin> --simulate --output rehearsal\usbc_sim_01.jsonl` ends with error 561 and a clean shutdown (simulated runs go in `rehearsal\`, never `logs\`).

### 2. Set up capture

- [ ] Wireshark open as Administrator, `USBPcap1`, `USBPcap2`, ... listed. **Start the capture on the controller's hub before the controller is powered or plugged in**, so the connect handshake and the version reply are recorded.
- [ ] Only one program may talk to the controller. Close everything else.
- [ ] Power the controller, then start SCORBASE. Save each capture as `intelitek_<letter>_<name>.pcapng` in `logs\`, exact names below.

### 3. Hand sweep with SCORBASE, motors off (gate for the roll switch)

SCORBASE shows the encoder counts and the home-switch status of every axis (Information bar, "Home switch status of all eight axes"). Click **Control Off** first so the motors are not driving.

- [ ] Capture `intelitek_S_handsweep.pcapng` running.
- [ ] One joint at a time, move it slowly by hand through its whole range, watching the counts and its switch indicator. For each joint write down: **which physical direction made the counts rise**, the counts where the switch indicator turns **on** and where it turns **off**, and the counts at both ends of travel.
- [ ] **Wrist roll (the gate):** roll the wrist by hand through its switch. The roll coordinate is the mean of motors 4 and 5. Width = the absolute difference between (mean of motors 4 and 5 at ON) and (mean at OFF), in counts; it does not matter which way you rolled.
  - **Both edges seen and the width about 800 or more:** carry on.
  - **The switch is still on when the wrist runs out of travel:** the width is at least the distance from the ON edge to the end of travel. If that is about 800 or more, carry on; if not, or an edge was missed, sweep again.
  - **Narrower than that:** keep the capture and tell the owner before running any vendor home.
- [ ] Move gently, don't push a joint into its stop. If a joint runs out of travel before its switch turns off, write that down.

### 4. Connect, control on and off (SCORBASE)

- [ ] Capture `intelitek_D_control.pcapng`: arm at rest, write down the shown position, **Control Off**, wait 5 s, **Control On**, wait 5 s, write the position, **Control Off**. Photograph the LEDs after each click and note how long it took.

### 5. SCORBASE Home (the main capture; answers question 1 and part of 3)

The operator checks, and the agent notes: the arm is within a few tens of degrees of its home pose, the jaws are empty, the wrist path is clear, a person has a hand on the physical stop, the roll gate in step 3 passed.

- [ ] Capture `intelitek_A_home.pcapng`. Operator: connect, idle 60 s with motors on, **Home** (robot), write the position SCORBASE shows, **Home a second time**, jog the base about 1 degree, write the direction, close.
- [ ] Note: the shown position after each home (the same both times?), whether the gripper moved, the wrist pitch move size, how long each home took.

### 6. Stop during a move (answers question 2)

- [ ] Capture `intelitek_F_stop.pcapng`. In SCORBASE, teach one position a few degrees away from home first (jog there, teach it, then home again or jog back). Then start a **go to** for that position and press **F9 Stop** mid-move. Note: did it stop at once, did control stay on?

### 7. E-stop at rest (optional; skip if short on time)

- [ ] Capture `intelitek_E_estop.pcapng`: arm at rest, write the position, wait 5 s, press the **physical e-stop**, wait 5 s, release per the lab procedure, note what SCORBASE shows, Control On, write the position. Photograph the LEDs.

### 8. `usbc_home` (only if steps 3 to 5 went cleanly, the operator agrees, and time is left)

Close SCORBASE completely first (only one program can connect). Same checks as step 5, and the step 3 roll gate matters here: the DLL's roll search has no distance limit.

- [ ] `<bin>\python.exe tools\usbc_home\usbc_home.py --dll-dir <bin> --online --no-home --operator "<name>" --acknowledge-vendor-home --output logs\usbc_nohome_01.jsonl`; the operator types `CONNECT`.
- [ ] Then, with USBPcap capturing as `usbc_H_home.pcapng`: the same without `--no-home`, `--output logs\usbc_home_01.jsonl`; the operator types `HOME`, and presses `S` (calls `Stop`) only if something looks wrong. Pass: callbacks 1 to 5 then `0x40`, exit code 0.

### 9. Did the home survive our connect? (last; our code runs for the first time)

If step 5 or 8 completed, nothing was moved since, and the operator agrees. Swap the driver to WinUSB (photograph Driver Provider before and after), then the read-only recorder: `python examples\record_raw_state.py` (see `docs\lab\LAB_DAY_CARD.md` step A for flags). Leave the arm where step 5 ended, a degree off home after the base jog (write down the counts SCORBASE showed there). If the recorder reads about that offset, the home survived; if it reads about 0, our connect reset it. Counts near 0 at the home pose itself prove nothing, because a fresh power-on also reads 0 there.

## Before you leave

- [ ] Copy the **whole** `logs\` folder, every `.pcapng`, and the notes to the stick **and** the cloud.
- [ ] Photograph the handwritten notes.
- [ ] Write the notes block for each step: step, file name, what the operator saw, the numbers, anything unexpected. No interpretation beyond what the file shows.

## Back at the desk

1. `python scripts\usb_trace.py summary <file>.pcapng` to find the controller's device number, then `python scripts\usb_trace.py export <file>.pcapng --device <n> --out <file>.jsonl`, then `python scripts\replay_vendor_home.py <file>.jsonl` (the first differing command wins).
2. Put the real switch bands, timing and version reply into `scorbot/vendor_home_model.py` (`BANDS`; `tests/vendor_home_plant.py` only imports it); re-run the port tests.
3. Record each answer in `docs/protocol/BRING_UP_SEQUENCE.md` with the file name. If the roll width was under 800, tell the owner before any visit B.
