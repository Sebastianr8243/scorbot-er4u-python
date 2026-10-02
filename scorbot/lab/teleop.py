"""Teleop mode of the guided lab session: arm once, then one press = one step.

Every step goes through the session's own gates (_prepare: travel cap, drift
check, preview; _execute: jog_joint and logging); teleop only removes the
typed confirmation and the questions between steps. After each step the
operator layer waits until the key is physically released and drops the
queued auto-repeats, so a held key gives exactly one step. Episodes mark
spans of a session for datasets. The physical stop is the stop.
"""

from __future__ import annotations

TELEOP_IDLE_DISARM_S = 15.0
TICK_S = 0.2
HELP = ("TELEOP: 1/q base +/-  2/w shoulder +/-  3/e elbow +/-  r start/stop episode  "
        "n new task  t leave teleop  ? help  x finish.  One press = one step; release "
        "the key before the next. Any other key disarms. The physical stop is the stop.")


class Teleop:
    def __init__(self, session, camera=None):
        # Episode numbers and the task live on the session, so leaving and
        # re-entering teleop never reuses an episode number.
        self.s, self.camera = session, camera
        self.episode: dict | None = None

    # -- loop -----------------------------------------------------------------

    def run(self) -> str:
        """Run until the operator leaves ("left") or finishes ("finish")."""
        s = self.s
        if not s.op.can_wait_for_release():
            reason = "needs single-key input with key-release detection (Windows console)"
            s._write("teleop_refused", reason=reason)
            s.op.show(f"Teleop {reason}.", "warn")
            return "left"
        s._write("teleop_start", idle_disarm_s=TELEOP_IDLE_DISARM_S)
        s.op.show(HELP)
        s.op.wait_for_release("t")
        result, last = "left", s.clock()
        try:
            while s.fault is None and s.armed:
                s._status()
                key = s.op.key_or_tick("teleop key: ", TICK_S)
                self._check_camera()
                if key is None:
                    if s.clock() - last > TELEOP_IDLE_DISARM_S:
                        s._disarm(f"teleop idle for more than {TELEOP_IDLE_DISARM_S:g} s")
                    continue
                outcome = self._handle(key)
                if outcome is not None:
                    result = outcome
                    break
                last = s.clock()
        finally:
            reason = ("fault" if s.fault else "disarmed" if not s.armed else
                      "finish" if result == "finish" else "left teleop")
            self._end_episode("aborted", reason)
            s._write("teleop_end", result=result)
        return result

    def _intent(self, key, action, accepted, reason=None, **extra):
        self.s._write("teleop_intent", key=key, action=action, accepted=accepted,
                      reason=reason, **extra)

    def _handle(self, key):
        from .session import JOG_KEYS
        s = self.s
        if key in ("", "x"):
            self._intent(key, "finish", True)
            return "finish"
        if key == "t":
            self._intent(key, "leave", True)
            s.op.wait_for_release(key)
            return "left"
        if key == "?":
            self._intent(key, "help", True)
            s.op.show(HELP)
        elif key == "r":
            # Release first: held-key repeats must never reach the task prompt.
            s.op.wait_for_release(key)
            self._toggle_episode()
        elif key == "n":
            s.op.wait_for_release(key)
            self._new_task()
        elif key in JOG_KEYS:
            self._jog(key, *JOG_KEYS[key])
        else:
            self._intent(key, "unknown", False, "unknown key disarms")
            s._disarm(f"unknown key {key!r}")
        return None

    def _jog(self, key, joint, sign):
        s = self.s
        delta = sign * s.step
        prepared = s._prepare(joint, delta)
        accepted = False
        if prepared is not None:
            n, before, plan = prepared
            accepted = s._execute(joint, delta, "teleop", n, before, plan, observe=False)
            if accepted and self.episode is not None:
                self.episode["jogs"] += 1
        discarded = s.op.wait_for_release(key)
        self._intent(key, "jog", accepted, None if accepted else "refused or failed",
                     joint=joint, delta_deg=delta, discarded_keys=discarded)

    # -- episodes ---------------------------------------------------------------

    def _new_task(self):
        s = self.s
        if self.episode is not None:
            self._intent("n", "new_task", False, "episode open")
            s.op.show("Stop the episode first (r) to close the episode, then change the task.")
            return
        task = s.op.text("Task for the next episodes (e.g. reach left block): ")
        if not task:
            self._intent("n", "new_task", False, "empty task")
            return
        s.episode_task = task
        self._intent("n", "new_task", True)

    def _toggle_episode(self):
        if self.episode is not None:
            self._end_episode("completed")
            return
        s = self.s
        if self.camera is not None and not self.camera.live():
            reason = self.camera.problem()
            self._intent("r", "episode_start", False, reason)
            s.op.show(f"Episode not started: {reason}.", "warn")
            return
        if s.episode_task is None:
            task = s.op.text("Task for these episodes (e.g. reach left block): ")
            if not task:
                self._intent("r", "episode_start", False, "empty task")
                s.op.show("Episode not started: a task is needed.", "warn")
                return
            s.episode_task = task
        s.episode_count += 1
        number, task = s.episode_count, s.episode_task
        self.episode = {"episode": number, "task": task, "jogs": 0,
                        "frames_at_start": (self.camera.frames_written()
                                            if self.camera is not None else 0)}
        self._intent("r", "episode_start", True)
        s._write("episode_start", episode=number, task=task, camera=self.camera is not None)
        s.rec.log_episode(number, "start", task=task)
        s.op.show(f"EPISODE {number} RECORDING: {task}. Press r to stop.")

    def _end_episode(self, status, reason=None):
        episode = self.episode
        if episode is None:
            return
        frames = (self.camera.frames_written() - episode["frames_at_start"]
                  if self.camera is not None else None)
        if status == "completed" and self.camera is not None:
            if not self.camera.live():
                status, reason = "aborted", self.camera.problem()
            elif frames == 0:
                status, reason = "aborted", "no camera frames recorded in this episode"
        self.episode = None
        s = self.s
        s._write("episode_end", episode=episode["episode"], status=status, reason=reason,
                 jogs=episode["jogs"], frames=frames)
        s.rec.log_episode(episode["episode"], "end", task=episode["task"], status=status,
                          reason=reason)
        level = "info" if status == "completed" else "alarm"
        s.op.show(f"EPISODE {episode['episode']} {status.upper()}"
                  + (f": {reason}" if reason else "") + ".", level)

    # -- camera -----------------------------------------------------------------

    def _check_camera(self):
        if self.camera is None:
            return
        for row in self.camera.drain():
            self.s._write("camera_health", **row)
        if self.episode is not None and not self.camera.live():
            self._end_episode("aborted", self.camera.problem())
