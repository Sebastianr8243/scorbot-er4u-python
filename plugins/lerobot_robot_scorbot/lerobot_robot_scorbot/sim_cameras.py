"""The MuJoCo twin's ``front`` and ``wrist`` cameras as LeRobot cameras (``type: scorbot_twin``). Needs lerobot.

``ScorbotRobot`` poses the twin from the simulator's encoder counts on every ``get_observation``
(``scorbot.mujoco_twin.chain_angles``), then reads these cameras, so each frame shows the pose in the same
observation. The scene is nominal solids, not CAD; the gripper is fixed open and the block is never pushed
(the arm is posed, not simulated). ``mujoco`` is imported on connect only. Off-screen rendering that fails
(no OpenGL) raises with the reason; nothing falls back to a blank frame.

    --robot.cameras='{front: {type: scorbot_twin, camera: front, width: 320, height: 240},
                      wrist: {type: scorbot_twin, camera: wrist, width: 320, height: 240}}'
"""

from dataclasses import dataclass
import threading
from typing import Any

from lerobot.cameras import Camera, CameraConfig

from scorbot import mujoco_twin

MAX_WIDTH, MAX_HEIGHT = 640, 480        # the scene's off-screen buffer (mujoco_twin.mjcf)


@CameraConfig.register_subclass("scorbot_twin")
@dataclass
class ScorbotTwinCameraConfig(CameraConfig):
    camera: str = "front"           # a camera in the twin scene: front or wrist
    width: int = 320
    height: int = 240
    fps: int = 30                   # LeRobot requires one; frames are rendered on demand at any rate

    def __post_init__(self):
        if self.camera not in mujoco_twin.CAMERAS:
            raise ValueError(f"camera must be one of {mujoco_twin.CAMERAS}, not {self.camera!r}")
        if not (0 < self.width <= MAX_WIDTH and 0 < self.height <= MAX_HEIGHT):
            raise ValueError(f"a twin frame is at most {MAX_WIDTH}x{MAX_HEIGHT}, not {self.width}x{self.height}")


class TwinScene:
    """One MuJoCo model and data that the robot poses and its twin cameras render."""

    def __init__(self):
        self.model, self.data = mujoco_twin.load()
        self.lock = threading.Lock()

    def set_counts(self, counts_from_home: dict[str, int]) -> None:
        with self.lock:
            mujoco_twin.set_pose(self.model, self.data, mujoco_twin.chain_angles(counts_from_home))


class ScorbotTwinCamera(Camera):
    """Renders one twin camera. ``ScorbotRobot`` attaches its scene before connecting it."""

    def __init__(self, config: ScorbotTwinCameraConfig):
        super().__init__(config)
        self.config = config
        self.scene: TwinScene | None = None
        self._renderer = None

    def attach(self, scene: TwinScene) -> None:
        self.scene = scene

    @property
    def is_connected(self) -> bool:
        return self._renderer is not None

    @staticmethod
    def find_cameras() -> list[dict[str, Any]]:
        return [{"type": "scorbot_twin", "id": name} for name in mujoco_twin.CAMERAS]

    def connect(self, warmup: bool = True) -> None:
        if self.scene is None:
            raise RuntimeError("a scorbot_twin camera works only as one of the scorbot robot's cameras "
                               "(--robot.type=scorbot --robot.simulate=true)")
        import mujoco
        try:
            self._renderer = mujoco.Renderer(self.scene.model, self.height, self.width)
        except Exception as error:
            raise RuntimeError(f"MuJoCo off-screen rendering failed ({type(error).__name__}: {error}); "
                               "twin cameras need OpenGL") from error
        if warmup:
            self.read()

    def read(self):
        if self._renderer is None:
            raise ConnectionError(f"twin camera {self.config.camera} is not connected")
        with self.scene.lock:
            self._renderer.update_scene(self.scene.data, camera=self.config.camera)
            return self._renderer.render().copy()

    def async_read(self, timeout_ms: float = 200):
        return self.read()          # rendered on demand: every frame is of the latest pose

    def read_latest(self, max_age_ms: int = 500):
        return self.read()

    def disconnect(self) -> None:
        renderer, self._renderer = self._renderer, None
        if renderer is not None:
            renderer.close()
