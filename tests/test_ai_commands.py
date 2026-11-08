"""Command previews never train, transfer files or open hardware."""

import contextlib
import io
import unittest
from unittest import mock

from scripts import ai_commands
from scorbot import cli


def preview(*args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert ai_commands.main(list(args)) == 0
    return output.getvalue()


class CommandTests(unittest.TestCase):
    def test_train_defaults_and_copy_hint(self):
        output = preview("train")
        for flag in ("lerobot-train", "--policy.type=act", "--policy.device=cuda",
                     "--steps=20000", "--batch_size=8", "--policy.push_to_hub=false",
                     "--dataset.repo_id=local/my_first_demo",
                     "--dataset.root=rehearsal/my_first_demo", "--output_dir=outputs/act",
                     "scp -r", "<user>@<gpu-pc>:<path>"):
            self.assertIn(flag, output)

    def test_smolvla_uses_pretrained_path(self):
        output = preview("train", "--policy", "smolvla")
        self.assertIn("--policy.path=lerobot/smolvla_base", output)
        self.assertNotIn("--policy.type=act", output)

    def test_train_overrides_and_powershell_quoting(self):
        output = preview("train", "--dataset-root", "C:/demo's data", "--repo-id", "lab/demo",
                         "--output-dir", "outputs/demo", "--steps", "12",
                         "--batch-size", "2", "--device", "cpu")
        for flag in ("'--dataset.root=C:/demo''s data'", "--dataset.repo_id=lab/demo",
                     "--output_dir=outputs/demo", "--steps=12", "--batch_size=2",
                     "--policy.device=cpu"):
            self.assertIn(flag, output)

    def test_rollout_always_disables_return_and_reuses_record_gates(self):
        for sim in ([], ["--sim"]):
            output = preview("rollout", "--model", "models/act", *sim)
            for flag in ("lerobot-rollout", "--strategy.type=base", "--policy.path=models/act",
                         "--robot.type=scorbot", "--return_to_initial_position=false",
                         *cli.ROBOT_SAFETY_FLAGS):
                self.assertIn(flag, output)
            self.assertEqual("--robot.simulate=true" in output, bool(sim))
            self.assertIn("--device=cpu", output)

    def test_preview_never_launches_a_process(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("process launched")):
            preview("train")
            preview("rollout", "--model", "models/act", "--sim")


if __name__ == "__main__":
    unittest.main()
