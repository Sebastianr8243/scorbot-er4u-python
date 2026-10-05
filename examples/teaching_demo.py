"""A first script with the teaching API. SIMULATED: never opens USB, never moves the arm.

    python examples/teaching_demo.py

Angles and positions come from the source model (vendor formula and parameter
files), not from measurements of our arm.
"""

from scorbot.toolbox import Arm


def show(arm, label):
    angles = arm.get_angles()
    x, y, z, pitch, _roll = arm.get_xyz()
    print(f"{label:<12} base {angles['base']:7.2f}  shoulder {angles['shoulder']:7.2f}  "
          f"elbow {angles['elbow']:7.2f} deg   tool ({x:6.1f}, {y:6.1f}, {z:6.1f}) mm, "
          f"pitch {pitch:6.1f} deg")


if __name__ == "__main__":
    with Arm() as arm:
        print(arm.status)
        arm.go_home()
        show(arm, "home")
        arm.move_by_angles(base=5, elbow=3)
        show(arm, "joint move")
        x, y, z = arm.get_xyz()[:3]
        arm.move_to_xyz(x + 15, y, z - 10)
        show(arm, "tool move")
        arm.set_gripper("close")
        arm.set_gripper("open")
        arm.go_home()
        show(arm, "home again")
