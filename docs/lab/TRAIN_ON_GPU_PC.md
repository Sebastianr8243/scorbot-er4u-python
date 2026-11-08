# Train on the GPU PC, run on the lab PC

The lab PC records demonstrations without a GPU. Copy the dataset to the Windows
GPU workstation over SSH, train there, then copy the model back. This is the
planned lab workflow; hardware, SSH connectivity, CPU rollout speed, and physical
arm behavior are **unverified**. A 20 GB GPU is a rough starting estimate for ACT
and SmolVLA, **unverified** for our images and batch sizes. Start with ACT.

## 1. Prepare both PCs

From this repo's checkout on each PC, run:

```powershell
scorbot setup lerobot
```

This builds the separate LeRobot environment and runs the simulation check.
It expects Python 3.12; the default environment is `$HOME/envs/lerobot-stack2`.
Activate it before running any printed LeRobot command:

```powershell
& "$HOME/envs/lerobot-stack2/Scripts/Activate.ps1"
```

On the GPU PC, enable Windows **Developer Mode** (search Settings for it) before
training. Otherwise creating the `checkpoints/last` symbolic link can crash with
WinError 1314; see [LeRobot issue #4059](https://github.com/huggingface/lerobot/issues/4059).
LeRobot saves the numbered checkpoint's model files before updating that link,
so check the numbered folder if this happens. The first default ACT run downloads
ResNet18 weights; allow internet access and time for that download.
The setup command does not verify that PyTorch can use the GPU. In its environment,
check `python -c "import torch; print(torch.cuda.is_available())"`; resolve a false
result before choosing CUDA training.

## 2. Record and transfer the dataset

Use the supervised [arm runbook](RUN_THE_ARM.md) before any real recording.
`scorbot record` only prints a **simulation** recording command. Rehearse that first;
real demonstrations and matching camera configuration remain **unverified**.
Keep the same camera names, image sizes and observation features for training and
rollout. Copy the entire dataset folder, including metadata and videos.

Replace `student`, `gpu-pc`, and the destination with your SSH account, hostname,
and an existing writable folder. Example from the lab PC:

```powershell
scp -r rehearsal/my_first_demo student@gpu-pc:datasets/my_first_demo
ssh student@gpu-pc
```

The GPU PC needs an SSH server configured; network access is **unverified**.

## 3. Train on the GPU PC

In the GPU PC checkout with its LeRobot environment active:

```powershell
scorbot ai train --dataset-root datasets/my_first_demo --repo-id local/my_first_demo --output-dir outputs/act
```

This prints a command only. Review it, then paste just the `lerobot-train` command
into PowerShell. Defaults: ACT, CUDA, 20,000 steps, batch size 8, no Hub upload.
Choose a new output directory for each run. Lower `--batch-size` if GPU memory runs
out; memory needs are **unverified** here.

Try SmolVLA later with `--policy smolvla --output-dir outputs/smolvla`. It loads
`lerobot/smolvla_base` instead of training ACT. Its download, dataset compatibility,
and 20 GB memory fit are **unverified** on the workstation.

## 4. Copy the model back and rehearse rollout

From the lab PC, replace the example checkpoint number with an actual saved one:

```powershell
scp -r student@gpu-pc:outputs/act/checkpoints/020000/pretrained_model models/act
scorbot ai rollout --model models/act --sim
```

Create the local `models` parent folder first if absent. Paths on the GPU PC are
relative to its SSH login folder; use the full checkout path if needed.
The helper prints a `lerobot-rollout` command; run it in the lab PC's LeRobot venv.
It selects CPU inference for the lab PC and always includes
`--return_to_initial_position=false`, preventing LeRobot's automatic shutdown
return move. Keep that flag on every rollout. The printed operator-at-stop,
empty-jaws and clear-wrist flags are declarations the operator must check.

Without `--sim`, the helper still only prints a command, but executing it requests
real hardware. Follow the runbook and the plugin's real-arm gates first; this
helper supplies no calibration or real-arm authorization. Physical rollout and
CPU performance are **unverified**. A no-GPU lab PC may be too slow.

## Cameras and later options

- Test a USB webcam with `scorbot camera check`. USB compatibility, mounting and
  frame rate are **unverified** for the lab camera.
- The Seeed XIAO ESP32S3 Sense is a WiFi camera candidate. An MJPEG firmware stream
  could use `scorbot camera check --url http://CAMERA/STREAM`; firmware, URL,
  LeRobot `mjpeg` type and compatibility are **unverified**. Use the firmware's
  actual stream URL, not this placeholder.
- Longer term: Intel RealSense D405, LeRobot type `intelrealsense`. Camera support
  is **unverified** here. Plan color-only observations for ACT/SmolVLA; depth use
  and model compatibility are **unverified**.
- Optional later: LeRobot async `policy_server`/`robot_client` through an `ssh -L`
  tunnel could keep inference on the GPU PC. Commands, version compatibility,
  security configuration, timing and arm behavior are **unverified**; this guide
  does not set that route up.

Flag names were checked against the installed LeRobot 0.6.1 `configs/train.py`,
`configs/policies.py`, and `rollout/configs.py`. The Windows symlink issue above
and the local checkpoint save order support the Developer Mode advice; neither
is a successful training or hardware test on these PCs.
