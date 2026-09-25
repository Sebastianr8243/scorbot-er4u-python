"""Offline motor-count planning for legacy relative jogs.

The scales are inherited openScorbot software assumptions, not calibration of
a physical arm. A plan describes requested controller setpoints only.
"""

from math import ceil, isfinite

COUNTS_PER_DEGREE = {
    "base": 2837 / 20,
    "shoulder": 2300 / 20,
    "elbow": 2252 / 20,
    "wrist_pitch": 676 / 20,
    "wrist_roll": 558 / 20,
}
ORDER_TO_JOINT = {
    4: "base", 5: "base",
    6: "shoulder", 7: "shoulder",
    8: "elbow", 9: "elbow",
    10: "wrist_pitch", 11: "wrist_pitch",
    12: "wrist_roll", 13: "wrist_roll",
}
MOTOR_DIRECTIONS = {
    4: (-1, 0, 0, 0, 0), 5: (1, 0, 0, 0, 0),
    6: (0, 1, 0, 0, 0), 7: (0, -1, 0, 0, 0),
    8: (0, 0, -1, 0, 0), 9: (0, 0, 1, 0, 0),
    10: (0, 0, 0, -1, 1), 11: (0, 0, 0, 1, -1),
    12: (0, 0, 0, 1, 1), 13: (0, 0, 0, -1, -1),
}
MOTOR_NAMES = ("base", "shoulder", "elbow", "wrist_motor_1", "wrist_motor_2")


def counts_for_degrees(joint, degrees):
    """Nearest integer motor count under the inherited software scale."""
    if joint not in COUNTS_PER_DEGREE:
        raise ValueError("Unknown joint")
    if isinstance(degrees, bool) or not isinstance(degrees, (int, float)) or not isfinite(degrees):
        raise ValueError("Degrees must be finite")
    counts = round(round(abs(degrees) * COUNTS_PER_DEGREE[joint], 2))
    if counts < 1:
        raise ValueError("Requested jog is smaller than one motor count")
    return counts


def increments_for_counts(counts, speed):
    """Integer speed-shaped increments whose sum is exactly counts."""
    if type(counts) is not int or counts < 1:
        raise ValueError("Motor count target must be a positive integer")
    if type(speed) is not int or not 1 <= speed <= 20:
        raise ValueError("Legacy speed must be an integer from 1 to 20")
    steps = max(1, ceil(counts / speed))
    while True:
        caps = [min(speed, max(1, ceil(speed * (i + 1) / 12)),
                    max(1, ceil(speed * (steps - i) / 12)))
                for i in range(steps)]
        total = sum(caps)
        if total >= counts:
            break
        steps += 1
    quotas = [counts * cap / total for cap in caps]
    values = [int(quota) for quota in quotas]
    remaining = counts - sum(values)
    order = sorted(range(steps), key=lambda i: (quotas[i] - values[i], caps[i]), reverse=True)
    for i in order[:remaining]:
        values[i] += 1
    result = tuple(value for value in values if value)
    if sum(result) != counts or any(value > speed for value in result):
        raise AssertionError("Invalid jog profile")
    return result


def plan_jog(order, degrees, speed):
    """Return an offline preview for one legacy order code."""
    if order not in ORDER_TO_JOINT:
        raise ValueError("Unknown jog order")
    joint = ORDER_TO_JOINT[order]
    counts = counts_for_degrees(joint, degrees)
    increments = increments_for_counts(counts, speed)
    directions = MOTOR_DIRECTIONS[order]
    return {
        "joint": joint,
        "order": order,
        "requested_degrees": degrees,
        "speed": speed,
        "counts_per_motor": counts,
        "increments": increments,
        "motor_count_deltas": {
            name: direction * counts
            for name, direction in zip(MOTOR_NAMES, directions) if direction
        },
        "scale_status": "legacy assumption; not physically calibrated",
    }
