"""The limits several modules must agree on, in one place.

These were written out separately in the SDK, the lab session, the follower
and the exporter, each with a comment naming the others. Change a limit here
and nowhere else. Pure: standard library only, and nothing in ``scorbot`` is
imported, so any module may import it.

None of these is measured on our arm. Raising a limit needs lab evidence and
an entry in project/PROJECT_LOG.md (in git history).
"""

# The motors a motion command may move. Wrist jogs stay disabled: the two
# wrist motors are coupled and unmeasured. Only an opt-in wrist stream
# (Scorbot.start_stream(wrist=True), PolicyArm(wrist=True)) moves them.
ARM_MOTORS = ("base", "shoulder", "elbow")
WRIST_MOTORS = ("wrist_motor_1", "wrist_motor_2")
# The five motors whose counts are logged with every action and exported.
RECORDED_MOTORS = ARM_MOTORS + WRIST_MOTORS

# Largest single jog the SDK accepts, in degrees. Directions and scales are
# inherited, not measured.
MAX_JOG_DEG = 5.0

# Optional extra travel from session home, in degrees. None uses only the
# vendor limit (USBC.dll EncLimit / ROB_4u.INI), not measured on our arm.
# The 5 degree jog ceiling and the stream's lead limit still apply.
TRAVEL_CAP_DEG = None

# A legacy jog ends once the joint is within 20 counts of its target
# (openScorbot/libcomm.py settle loop), so counts may keep settling that far.
# Used as: drift between steps, wrist tolerance, export target tolerance, and
# how far an arm motor may read differently after a gripper move.
DRIFT_COUNTS = 20

# Readings this close together count as the arm at rest (our lab idle band).
STABLE_COUNTS = 2
