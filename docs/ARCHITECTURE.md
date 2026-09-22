# ER-4U Python SDK: systems review

**Status:** Proposed, with a small Python adapter implemented for bench evaluation

**Date:** 2026-09-22
**Decision:** Preserve the existing USB packet path as a legacy backend while moving application code to a controller-neutral Python API. Do not expose autonomous or Cartesian motion until feedback, limits, and stop behavior are measured on the actual robot.

## 1. Executive summary

The repository already contains USB packet construction, synchronization, homing, and relative motion. Its control logic is split across GUI callbacks and global queues, and several physical assumptions are undocumented. The first milestone is one supervised Python process that connects, reads raw encoders, homes from the required start pose, makes one bounded joint jog, and disconnects. The `scorbot` adapter provides that interface for evaluation; it is **not hardware-validated**.

## 2. Existing architecture assessment

| Source | Current role | Decision |
|---|---|---|
| `openScorbot/gui.py:main`, `set_conection` | USB discovery, handshake, threads, UI | Move control ownership out of GUI; keep UI as reference |
| `openScorbot/libsync.py:msg_start`, `syncro` | Initial USB exchange and idle synchronization | Preserve packets and timing, then characterize on hardware |
| `openScorbot/libcomm.py:execute`, `move_hips`, `move_shoulder`, `move_elbow`, `move_wrist` | Command dispatch and motor packet loops | Wrap for v0.1; split command/result queues; later extract controller driver |
| `openScorbot/libdef.py:set_msg`, `check`, `get_media`, `builder` | Packet I/O, encoder math, GUI helpers, IK | Separate these responsibilities over time |
| `openScorbot/setHome.py:homing` | Sequential switch-seeking routine | Retain only under a documented start-pose precondition; replace with a verified state machine |
| `openScorbot/moveXYZ.py:controlXYZ` | Cartesian target conversion and execution | Do not expose yet; validate kinematics and trajectory behavior first |
| `openScorbot/conf.py:readData`, `setup` | Legacy defaults and JSON file | Keep for compatibility; replace with validated typed calibration |
| `src/robot.py` | License text only | No control functionality to reuse |

**Known from code:** the USB ID is `09F1:0007`; `send_pkt3` calls `motorson`; encoder offsets are `[19,24,29,34,39,44]`; home switches use response byte 5; `libcomm.execute` dispatches numeric commands 4–19. **Inferred:** the six encoder entries correspond to base, shoulder, elbow, two wrist motors, and gripper, based on `conf.py` comments. **UNKNOWN — requires hardware/protocol verification:** packet semantics beyond the implementation, motor sign, exact speed units, absolute joint zero, controller-side watchdog, stop latency, and whether all errors are reported by the code.

## 3. Proposed system architecture

The SDK owns connection lifecycle, named commands, state, safety gating, and logging. One backend owns the original USB connection and serializes packet exchanges. Motion planning and model inference are clients of this interface and cannot write USB packets directly.

## 4. System-level block diagram

```text
Script / later CLI / later GUI / later ROS 2 / later VLA
                         |
                         v
             Scorbot API + typed RobotState
                         |
              validation + safety state
                         |
             serialized ControllerBackend
                 /                 \
      LegacyUsbBackend       SimulatedBackend
                 |                 |
       packet/sync code       deterministic state
                 |
      original controller -> ER-4U

      Telemetry records commands, responses, and measured state at the API boundary.
```

## 5. Software module structure

Keep the current `scorbot/robot.py` and `scorbot/state.py` small. Add `scorbot/backends/legacy_usb.py` and `simulated.py` when the adapter is stable, then `safety.py`, `calibration.py`, and `telemetry.py`. Keep `openScorbot/` as a named legacy dependency until packets are captured and compared against a replacement driver. Avoid empty module scaffolding.

## 6. Responsibilities

`Scorbot` validates calls and manages the session. A backend owns I/O and ordered commands. A state decoder reports measurements and validity. Calibration converts counts to physical units. Safety validates limits and robot state. Motion generates feasible joint targets. Telemetry records synchronized observations and commands. The GUI and CLI call the SDK only.

## 7. Main Python classes/interfaces

```python
class ControllerBackend(Protocol):
    def connect(self) -> None: ...
    def read_state(self) -> RobotState: ...
    def execute(self, command: JointJog) -> CommandResult: ...
    def disable(self) -> None: ...
    def disconnect(self) -> None: ...
```

Use a typed controller backend as the SDK seam; a byte-level `Transport` belongs inside that backend. This keeps simulation from pretending to be a USB endpoint. `Scorbot` remains the public facade. `JointJog`, `CommandResult`, and an explicit fault type should be introduced before coordinated motion.

## 8. Command and data flow

```text
caller -> name/unit validation -> homed/fault/limit gate -> one command worker
       -> backend packet exchange -> result -> fresh state -> telemetry
```

Commands are synchronous in v0.1. Later async commands may return handles, but still use one serialized owner for the controller and bounded queue depth. A timeout marks the session faulted; it does not prove the arm stopped.

## 9. Robot state model

Current state contains UTC timestamp, six raw encoder counts, six raw controller error values, home switch bits, connection flag, software-tracked enabled/homed flags, and a fault string. `enabled` and `homed` currently reflect SDK command history, not independently verified controller status. Joint angles, velocities, and position error must remain unavailable until count-to-angle calibration and timing are validated. Add freshness, command ID, and explicit state validity next.

## 10. Hardware abstraction architecture

Implement `LegacyUsbBackend` from the existing packet path first, followed by a `SimulatedBackend` using the same command and state types. A future custom controller can implement the same backend protocol. Keep USB libraries out of public API imports. A driver may own a lower-level `Transport`, but do not require all transports to expose a fake identical timing model.

## 11. Safety architecture

Validate finite numeric inputs, command size, homed/enabled state, and one command at a time before touching the backend. Calibrated joint, velocity, acceleration, and workspace limits come next. On a validation failure, reject without sending a packet. On communication or following-error failure, latch a fault, reject further motion, and attempt disable only if the channel is responsive. The physical emergency stop is authoritative. The current queued `disable()` cannot interrupt a blocked operation, so it must not be labeled an emergency stop.

## 12. Homing state machine

Proposed states: `UNHOMED -> START_POSE_CONFIRMED -> SEARCHING_SWITCH -> BACKING_OFF -> APPROACHING_SWITCH -> ZEROING -> VERIFYING -> HOMED`, with `FAULT` from every active state. The current `setHome.homing` moves shoulder, elbow, pitch, roll, then base and explicitly assumes a particular physical start pose. Switch polarity, repeatability, per-axis timeout, and zero offsets are **UNKNOWN — requires hardware verification**. Preserve that precondition in v0.1.

## 13. Motion-control architecture

The initial `jog_joint(name, delta_degrees)` is a bounded **relative** motion; no absolute `move_joint(target_angle)` contract should be claimed yet. Legacy command IDs and encoder increments stay in the backend. Once calibrated angles and limits exist, add absolute single-joint targets. Coordinated motion must share one trajectory clock across all joints and check measured following error.

## 14. Kinematics architecture

Forward and inverse kinematics consume calibrated geometry and joint angles; neither belongs in USB packet formatting. Treat `libdef.cIn` and `moveXYZ.controlXYZ` as unvalidated references. Verify frames, dimensions, singularities, joint coupling, and reachability against measured poses before exposing `move_xyz`.

## 15. Trajectory architecture

For the first calibrated motion planner, use a trapezoidal velocity profile with explicit speed and acceleration limits and sample it at a measured controller update interval. It is simpler to audit than a high-order polynomial and supports predictable stop-distance checks. Add smoother profiles only if measured vibration or task performance requires them. Controller timing and supported command cadence are **UNKNOWN — requires hardware verification**.

## 16. Telemetry architecture

The current adapter can append JSONL events with the command and a raw state snapshot. Next record monotonic and UTC timestamps, command ID, requested target, measured position, speed, status, fault, and software version at both command start and end. Export a flattened CSV for experiments. Camera frames should carry synchronized timestamps and calibration metadata, but camera capture stays outside the low-level driver.

## 17. Simulation architecture

The simulator should use the same API and model connection, homing, bounded joint motion, and injectable faults. It is for software tests and student development. It must be visibly marked as simulated and should not be used to claim physical safety or protocol correctness.

## 18. CLI architecture

The later CLI should call `Scorbot`, with no packet logic of its own. Because a new command process disconnects when it exits, persistent `connect`, `status`, and `stop` shell commands need a single owner daemon or one interactive session. First provide a Python script; add CLI only after ownership and fault semantics are established.

## 19. ROS 2 integration strategy

Make ROS 2 an optional client that maps SDK state to `JointState` and commands to a controller adapter. Do not make ROS 2 a core dependency. Only publish calibrated angles as joint positions. Expose homing, fault, and connection state separately. MoveIt integration follows verified limits, URDF geometry, and collision models.

## 20. Configuration strategy

Use typed Python dataclasses for runtime validation, loaded from a versioned TOML file for each robot. TOML is in the Python standard library from 3.11; for Python 3.10 use a small parser dependency or JSON. Keep the current `data.json` only for the legacy backend. Calibration files should state units, joint direction, encoder zero, counts per degree, soft limits, speed/acceleration limits, and measurement provenance. Do not copy the current hard-coded values into a safety policy without measurement.

## 21. Testing strategy

Unit tests for validation, state decoding, and planners; protocol tests against captured packets; simulator tests for faults and queue ordering; supervised hardware-in-the-loop tests for connect, idle, homing, one-degree jogs, disconnect, and failure recovery. Unit, protocol, and simulator tests must **never** move the real robot. Hardware tests require an explicit opt-in and a physically present operator.

## 22. Version roadmap

**0.1:** preserve and bench-verify connect, raw encoder read, constrained homing, one bounded joint jog, disconnect. **0.2:** verified disable/stop behavior, fault state, calibrated state/limits, telemetry. **0.3:** simulator and coordinated joint motion. **0.4:** FK/IK and Cartesian planning. **0.5:** trajectory validation and repeatability experiments. **0.6:** camera time synchronization and perception interfaces. **0.7:** optional ROS 2 integration. VLA policies come only after the same safety and state contracts are exercised by deterministic applications.

## 23. Team task division

The technical lead owns USB packets, hardware tests, calibration acceptance, and safety review. One intern can build state/telemetry export and non-hardware tests. Another can build simulation, documentation, and CLI design. Both interns work through the SDK interface and use test fixtures; only reviewed changes to the backend touch hardware.

## 24. Major technical risks

The legacy handshake energizes motors; homing requires a known pose; the new 30-second switch-search deadline is provisional; `libsync.send_wait` has no explicit deadline; stop is queued behind motion; multiple modules assume shared mutable USB response data; some legacy math mixes UI, protocol, and kinematics. Controller firmware behavior, current limit interpretation, Windows USB driver binding, and watchdog support are unknown. These are bench-validation items, not assumptions to hide behind an SDK method.

## 25. Recommended first implementation task

Run the Python adapter in a supervised bench session and capture a golden trace: USB ID/backend, connect/disable result, several idle encoder packets, home switch bits at known poses, a one-degree base jog in each direction, and disconnect behavior. Compare the observed encoder changes and response timing with the code. Record the physical start pose and any fault. Only then promote this adapter from evaluation code to a verified v0.1 driver.
