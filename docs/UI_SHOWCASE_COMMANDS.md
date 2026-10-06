# Pre-lab simulator showcase: commands

Run these in **PowerShell**. First enter the repository root:

```powershell
cd C:\Users\sebas\startup\openScorbot
```

This showcase uses `SimulatedScorbot`; it does not connect to USB or move the physical arm. Its angles and positions come from the source model and have not been measured on this arm.

## 1. First-time setup on a PC

If `.venv` already exists and has the `ui` and `planning` extras, skip this section.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[ui,planning]"
```

The `ui` extra installs Viser for the browser page and its browser keyboard shortcuts; `planning` installs Ruckig for the simulated stream. No lab driver setup is needed. If you already installed the extras before this UI update, rerun the install command so Viser is at least version 1.1.1.

## 2. Start the browser showcase

```powershell
.\.venv\Scripts\python.exe -m scorbot.ui --simulate
```

Open `http://127.0.0.1:8080` in a browser. Keep this terminal open while presenting. If port 8080 is in use, choose another port:

```powershell
.\.venv\Scripts\python.exe -m scorbot.ui --simulate --port 8081
```

Then open `http://127.0.0.1:8081`. The server listens on this computer only.

## 3. Show what it does

These are browser actions, not terminal commands:

1. Point out **SIMULATED** and **source model, not measured** at the top.
2. Press `A` or `D` for a one degree base step; click its step buttons or drag its slider for other moves. Short key presses request discrete steps; holding a key may repeat them, but simulator travel limits still apply. The page shows current and requested joint angles; the blue arm is current and the orange outline is the target while it moves.
3. Use `W`/`S` for shoulder and `E`/`Q` for elbow, or use their buttons and sliders. Keep every joint within its displayed range; the shoulder has less than four degrees of upward travel from home in the source model. A step past a limit is refused and explained in the status area.
4. Drag the tool handle a small distance to demonstrate a millimetre target in the simulator.
5. Press **Open gripper**, **Close gripper**, and **Home**. The software stop is a UI control, not an emergency stop.

The page uses a line drawing when optional local link meshes are absent. No mesh download is required for this demo.

## 4. Show the teaching API (optional, second terminal)

```powershell
.\.venv\Scripts\python.exe examples\teaching_demo.py
```

This separate simulated run prints the home pose, a joint move, a tool move, gripper commands, and a return home. It does not control the browser session.

## 5. End the showcase

Press `Ctrl+C` in the browser-page terminal. Close the browser tab. The teaching demo exits on its own.

## 6. Quick checks if the page does not start

```powershell
.\.venv\Scripts\python.exe -c "import viser, ruckig; print('UI dependencies ready')"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_ui*.py" -q
```

If an import is missing, rerun the install command from section 1. The tests use the simulator and do not open USB.
