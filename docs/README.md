# Documentation index

**To run the arm: [`lab/RUN_THE_ARM.md`](lab/RUN_THE_ARM.md).** Project summary: [`../README.md`](../README.md). Where things stand: [`STATUS.md`](STATUS.md).

## API and overview

| Doc | Read when |
|---|---|
| [`API.md`](API.md) | Writing a script against `scorbot.bridge.Arm`: calls, units, errors |
| [`STATUS.md`](STATUS.md) | What works and how sure we are |
| [`STACK2_SDK.md`](STACK2_SDK.md) | The experimental own-USB-protocol SDK (`scorbot.Scorbot`); never moved the arm |

## lab/

| Doc | Read when |
|---|---|
| [`RUN_THE_ARM.md`](lab/RUN_THE_ARM.md) | Setting up the lab PC and moving the arm through the vendor DLL: probe, connect, jog, Home, point move, errors |
| [`BRING_UP_RUNBOOK.md`](lab/BRING_UP_RUNBOOK.md) | Stack 2 desk rehearsal and supervised probe, enable, Home, 1 degree jog/back and optional stream ladder |
| [`LAB_CHECKLIST.md`](lab/LAB_CHECKLIST.md) | Stack 2 first-visit page: driver path, Windows prep, probe order, restoring Intelitek's driver after a WinUSB swap |
| [`USB_CAPTURE.md`](lab/USB_CAPTURE.md) | Wireshark/USBPcap capture and comparison with this code's packets |
| [`PHYSICAL_CALIBRATION.md`](lab/PHYSICAL_CALIBRATION.md) | Measuring joint angles for calibration: file formats, priors, count wrap |
| [`WINDOWS_BENCH_RUN.md`](lab/WINDOWS_BENCH_RUN.md) | The pyusb path on Windows: install, driver check, preflight |

## design/, protocol/, manual/

| Doc | Read when |
|---|---|
| [`design/ARCHITECTURE.md`](design/ARCHITECTURE.md) | Changing module boundaries or adding a backend |
| [`design/TWO_STACKS_AND_AI.md`](design/TWO_STACKS_AND_AI.md) | Choosing between the vendor-DLL stack and our own USB stack, and what each means for an AI/LeRobot goal |
| [`design/STACK2_RESEARCH.md`](design/STACK2_RESEARCH.md) | What is established and what is unknown about driving the controller with our own USB code, after an independent claim check |
| [`protocol/PROTOCOL.md`](protocol/PROTOCOL.md) | Editing packet code, decoding a capture |
| [`protocol/VENDOR_DLL_PROTOCOL.md`](protocol/VENDOR_DLL_PROTOCOL.md) | What the vendor's `USBC.dll` sends (from disassembly) |
| [`protocol/BRING_UP_SEQUENCE.md`](protocol/BRING_UP_SEQUENCE.md) | The steps from power-on to a moving arm and how sure we are of each |
| [`protocol/ULPGC_THESIS_FINDINGS.md`](protocol/ULPGC_THESIS_FINDINGS.md) | The 2017 thesis that decoded SCORBASE's USB traffic |
| [`manual/ER4U_MODEL.md`](manual/ER4U_MODEL.md) | Every arm number we use and where it comes from |
| [`manual/HARDWARE_REFERENCE.md`](manual/HARDWARE_REFERENCE.md) | LEDs, limits, manual claims |
| [`manual/`](manual) | The rest of the manual audit (long) |

