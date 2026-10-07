"""The limits several modules must agree on, in one place.

These were written out separately in the SDK, the lab session, the follower
and the exporter, each with a comment naming the others. Change a limit here
and nowhere else. Pure: standard library only, and nothing in ``scorbot`` is
imported, so any module may import it.

None of these is measured on our arm. Raising a limit needs lab evidence and
an entry in docs/project/PROJECT_LOG.md.
"""

# The motors a motion command may move. Wrist jogs stay disabled: the two
# wrist motors are coupled and unmeasured.
ARM_MOTORS = ("base", "shoulder", "elbow")
WRIST_MOTORS = ("wrist_motor_1", "wrist_motor_2")
# The five motors whose counts are logged with every action and exported.
RECORDED_MOTORS = ARM_MOTORS + WRIST_MOTORS

# Largest single jog the SDK accepts, in degrees. Directions and scales are
# inherited, not measured.
MAX_JOG_DEG = 5.0

# Travel from this session's home, in degrees, for a stream, the lab session
# and the follower. Lifted from 10 on 2026-10-06 by the owner, without the lab
# evidence the staged widening in
# docs/specs/2026-10-04-streaming-driver-requirements.md asks for: the cap is
# now past every joint's range, so the joint limits of the source model
# (source_model.LIMITS_DEG, unmeasured) are the only travel bound. The 5 degree
# jog ceiling and the stream's lead limit still apply.
TRAVEL_CAP_DEG = 180.0

# A legacy jog ends once the joint is within 20 counts of its target
# (openScorbot/libcomm.py settle loop), so counts may keep settling that far.
# Used as: drift between steps, wrist tolerance, export target tolerance, and
# how far an arm motor may read differently after a gripper move.
DRIFT_COUNTS = 20

# Readings this close together count as the arm at rest (our lab idle band).
STABLE_COUNTS = 2
