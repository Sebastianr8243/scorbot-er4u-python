"""scorbot.lab.review.review_session_rows pinned: every problem message, its trigger, and the report shape.

A clean review is what lets an operator move on from a lab log, so each message and boundary is checked here
before the function is split into helpers.
"""

import unittest

from scorbot.lab.review import review_session_rows

LED_ROWS = [
    {"type": "led_observation", "step": "after_connect"},
    {"type": "led_observation", "step": "after_enable"},
]


def base(**session):
    return [{"type": "session", "robot_id": "arm-1", "data_source": "real", **session}, *LED_ROWS]


def jog(n, planned=None, measured=None, *, confirmed=True, after=True, failed=None, observation=None):
    rows = []
    if confirmed:
        rows.append({"type": "jog_confirmed", "n": n, "how": "typed", "move": f"BASE {n}"})
    if after:
        rows.append({"type": "after_jog", "n": n})
    if failed is not None:
        rows.append({"type": "jog_failed", "n": n, "error": failed})
    if observation is not None:
        rows.append({"type": "jog_observation", "n": n, **observation})
    if planned is not None or measured is not None:
        rows.append({"type": "jog_result", "n": n, "planned": planned, "measured": measured})
    return rows


def problems(*extra, start=None):
    return review_session_rows((base() if start is None else start) + [r for part in extra for r in part])["problems"]


class ReportShape(unittest.TestCase):
    def test_a_clean_log_reports_nothing_and_echoes_the_session(self):
        self.assertEqual(review_session_rows(base()),
                         {"data_source": "real", "robot_id": "arm-1", "jogs": [], "problems": [], "declined": None})

    def test_missing_session_row_and_defaults(self):
        report = review_session_rows(LED_ROWS)
        self.assertEqual(report["problems"], ["missing session row"])
        self.assertEqual((report["data_source"], report["robot_id"]), ("unknown", None))

    def test_session_without_data_source_is_unknown(self):
        report = review_session_rows([{"type": "session"}, *LED_ROWS])
        self.assertEqual(report["data_source"], "unknown")

    def test_declined_text_is_the_first_declined_row(self):
        rows = base() + [{"type": "operator_declined", "text": "first"}, {"type": "operator_declined", "text": "second"}]
        self.assertEqual(review_session_rows(rows)["declined"], "first")

    def test_problems_are_sorted_and_deduplicated(self):
        rows = base() + [{"type": "counts_drift", "differences": 1}, {"type": "session_failed", "error": "b"},
                         {"type": "counts_drift", "differences": 1}, {"type": "session_failed", "error": "a"}]
        self.assertEqual(review_session_rows(rows)["problems"],
                         ["counts drift before a step: 1", "session failed: a", "session failed: b"])

    def test_jog_rows_are_summarised_in_jog_order(self):
        rows = base() + jog(2, {"base": 5}, {"base": 5}, observation={"direction": "toward", "other_joint_moved": "no"}) \
            + jog(1, {"base": 3}, {"base": 4})
        jogs = review_session_rows(rows)["jogs"]
        self.assertEqual([j["n"] for j in jogs], [1, 2])
        self.assertEqual(jogs[1], {"n": 2, "move": "BASE 2", "how": "typed", "planned": {"base": 5},
                                   "measured": {"base": 5}, "direction": "toward", "other_joint_moved": "no"})
        self.assertEqual((jogs[0]["direction"], jogs[0]["other_joint_moved"]), (None, None))

    def test_a_jog_without_a_result_has_empty_plan_and_measurement(self):
        jogs = review_session_rows(base() + jog(1))["jogs"]
        self.assertEqual((jogs[0]["planned"], jogs[0]["measured"]), ({}, {}))

    def test_rows_with_a_non_integer_n_are_not_jogs(self):
        rows = base() + [{"type": "jog_confirmed", "n": "1"}, {"type": "jog_confirmed", "n": 1.0},
                         {"type": "jog_confirmed", "n": None}]
        report = review_session_rows(rows)
        self.assertEqual((report["jogs"], report["problems"]), ([], []))


class LogLevelProblems(unittest.TestCase):
    def test_session_failed(self):
        self.assertEqual(problems([{"type": "session_failed", "error": "boom"}]), ["session failed: boom"])

    def test_led_mismatch(self):
        row = {"type": "led_mismatch", "step": "after_enable", "led": "motors", "observed": "off", "expected": "lit"}
        self.assertEqual(problems([row]), ["LED mismatch after_enable: motors reported off, software expected lit"])

    def test_led_gate_failed(self):
        self.assertEqual(problems([{"type": "led_gate_failed", "reason": "dark"}]), ["LED gate failed: dark"])

    def test_missing_led_observations_each_named(self):
        self.assertEqual(problems(start=[{"type": "session"}, LED_ROWS[1]]), ["LED observation missing after_connect"])
        self.assertEqual(problems(start=[{"type": "session"}, LED_ROWS[0]]), ["LED observation missing after_enable"])

    def test_counts_drift(self):
        self.assertEqual(problems([{"type": "counts_drift", "differences": {"base": 9}}]),
                         ["counts drift before a step: {'base': 9}"])

    def test_park_not_parked_stopped_and_parked(self):
        self.assertEqual(problems([{"type": "park", "parked": False, "reason": "jam"}]), ["the arm was not parked: jam"])
        self.assertEqual(problems([{"type": "park"}]), ["the arm was not parked: None"])
        self.assertEqual(problems([{"type": "park", "parked": True}]), [])
        self.assertEqual(problems([{"type": "park_stopped"}]), ["the park was stopped: the arm is not at home"])


class PlanComplete(unittest.TestCase):
    def plan(self, answer: str | None = "yes", differences=None):
        return [{"type": "plan_complete", "name": "home", "answer": answer, "count_differences": differences}]

    def test_yes_with_small_or_missing_differences_is_clean(self):
        self.assertEqual(problems(self.plan()), [])
        self.assertEqual(problems(self.plan(differences={"base": 20, "elbow": -20})), [])

    def test_any_answer_but_yes_is_a_problem(self):
        self.assertEqual(problems(self.plan("no")), ["plan to home: arrival answer no, count differences None"])
        self.assertEqual(problems(self.plan(None)), ["plan to home: arrival answer None, count differences None"])

    def test_a_difference_past_the_tolerance_either_way_is_a_problem(self):
        self.assertEqual(problems(self.plan(differences={"base": 21})),
                         ["plan to home: arrival answer yes, count differences {'base': 21}"])
        self.assertEqual(len(problems(self.plan(differences={"base": -21}))), 1)

    def test_non_integer_differences_are_ignored(self):
        self.assertEqual(problems(self.plan(differences={"base": 99.5, "elbow": None, "x": "99"})), [])


class JogProblems(unittest.TestCase):
    def test_failed_before_motion_names_the_error_and_a_bare_unconfirmed_row_says_nothing(self):
        self.assertEqual(problems([{"type": "jog_failed", "n": 1, "error": "read"}]),
                         ["jog 1: failed before motion: read"])
        self.assertEqual(problems([{"type": "jog_preview", "n": 1}]), [])

    def test_started_without_after_jog(self):
        self.assertEqual(problems(jog(1, after=False)), ["jog 1: started but has no after_jog"])

    def test_failed_after_start_names_the_reason(self):
        self.assertEqual(problems(jog(1, after=False, failed="stall")), ["jog 1: failed: stall"])

    def test_failed_row_without_a_reason_reads_as_started(self):
        rows = jog(1, after=False) + [{"type": "jog_failed", "n": 1}]
        self.assertEqual(problems(rows), ["jog 1: started but has no after_jog"])

    def test_after_jog_present_means_no_start_problem_even_with_a_failed_row(self):
        self.assertEqual(problems(jog(1, failed="late")), [])

    def test_ambiguous_count_change(self):
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": None})), ["jog 1: base count change is ambiguous"])

    def test_moved_opposite_to_the_plan(self):
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": -1})), ["jog 1: base moved opposite to the plan"])
        self.assertEqual(problems(jog(1, {"base": -5}, {"base": 1})), ["jog 1: base moved opposite to the plan"])
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": 0})), [])
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": 3})), [])

    def test_a_motor_that_was_not_jogged_may_drift_up_to_the_tolerance(self):
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": 5, "shoulder": 20, "elbow": -20})), [])
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": 5, "shoulder": 21})),
                         ["jog 1: shoulder moved 21 counts but was not jogged"])
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": 5, "elbow": -21})),
                         ["jog 1: elbow moved -21 counts but was not jogged"])

    def test_only_motors_in_the_measurement_are_checked(self):
        self.assertEqual(problems(jog(1, {"base": 5}, {})), [])

    def test_a_missing_or_null_plan_means_nothing_was_jogged(self):
        self.assertEqual(problems(jog(1, None, {"base": 25})), ["jog 1: base moved 25 counts but was not jogged"])
        self.assertEqual(problems(jog(1, {}, {"base": 25})), ["jog 1: base moved 25 counts but was not jogged"])

    def test_only_listed_joints_are_checked(self):
        self.assertEqual(problems(jog(1, {"base": 5}, {"base": 5, "wobble": 999})), [])

    def test_each_jog_is_reported_under_its_own_number(self):
        rows = jog(2, {"base": 5}, {"base": None}) + jog(1, {"base": 5}, {"base": -5})
        self.assertEqual(problems(rows), ["jog 1: base moved opposite to the plan", "jog 2: base count change is ambiguous"])


if __name__ == "__main__":
    unittest.main()
