"""Pytest-only setup; the suite itself is plain unittest.

pytest-xdist runs every core at once (`-n auto`). On a slow or memory-starved
PC that makes Hypothesis's timing checks (the per-example deadline and the
"input generation is slow" health check) fail at random, although timing is
never what the property tests check. This profile turns both off.
"""

try:
    from hypothesis import HealthCheck, settings
except ImportError:  # optional dependency; property tests skip without it
    pass
else:
    settings.register_profile("suite", deadline=None,
                              suppress_health_check=[HealthCheck.too_slow])
    settings.load_profile("suite")
