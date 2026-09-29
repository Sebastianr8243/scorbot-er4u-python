"""Every recorded payload must validate against its advertised schema."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def validate(value, schema, path="$"):
    """Return a list of problems. Supports the JSON Schema subset the recorder uses."""
    problems = []
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_TYPES[kind](value) for kind in kinds):
            return [f"{path}: {value!r} is not {kinds}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: above maximum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}: missing required {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                problems += validate(value[key], sub, f"{path}.{key}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(schema.get("properties", {}))
            problems += [f"{path}: unexpected {key!r}" for key in sorted(extra)]
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{path}: too many items")
        for index, item in enumerate(value):
            problems += validate(item, schema.get("items", {}), f"{path}[{index}]")
    return problems


def read_messages(mcap_path):
    from mcap.reader import make_reader
    with open(mcap_path, "rb") as stream:
        return [(channel.topic, schema, json.loads(message.data))
                for schema, channel, message in make_reader(stream).iter_messages()]


class ValidatorSelfTests(unittest.TestCase):
    def test_validator_catches_the_basics(self):
        schema = {"type": "object", "required": ["a"],
                  "properties": {"a": {"type": ["integer", "null"]},
                                 "b": {"type": "object",
                                       "properties": {"c": {"type": "integer"}}}}}
        self.assertEqual(validate({"a": None, "b": {"c": 1}}, schema), [])
        self.assertTrue(validate({}, schema))
        self.assertTrue(validate({"a": True}, schema))
        self.assertTrue(validate({"a": 1, "b": {"c": "x"}}, schema))


class SchemaContentTests(unittest.TestCase):
    def test_schema_json_is_valid_with_real_properties(self):
        from scorbot.session import schemas
        from scorbot.state import JOINTS
        for name in schemas.REQUIRED:
            schema = json.loads(schemas.schema_json(name))
            self.assertTrue(schema["properties"], name)
            self.assertEqual(schema["required"], list(schemas.REQUIRED[name]))
            if name != schemas.IMAGE_SCHEMA:
                self.assertIs(schema["additionalProperties"], True)
                for key in schemas.REQUIRED[name]:
                    self.assertIn(key, schema["properties"], f"{name}.{key}")
        state = json.loads(schemas.schema_json("scorbot.RobotState"))["properties"]
        for field in ("encoder_counts", "signed_encoder_counts",
                      "controller_error_counts", "encoder_sign_bytes"):
            self.assertEqual(sorted(state[field]["properties"]), sorted(JOINTS), field)

    def test_every_robot_state_field_is_described(self):
        import dataclasses
        from scorbot.session import schemas
        from scorbot.state import RobotState
        described = schemas.PROPERTIES["scorbot.RobotState"]
        for field in dataclasses.fields(RobotState):
            self.assertIn(field.name, described)


class RecordedPayloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def check_all(self, mcap_path, expect_topics=()):
        messages = read_messages(mcap_path)
        seen = set()
        for topic, schema_record, payload in messages:
            schema = json.loads(schema_record.data)
            self.assertEqual(schema_record.encoding, "jsonschema")
            self.assertEqual(validate(payload, schema), [], f"{topic}: {payload}")
            seen.add(topic)
        for topic in expect_topics:
            self.assertIn(topic, seen)
        return messages

    def test_synthetic_session_validates_on_every_topic(self):
        from examples.make_synthetic_session import write_synthetic_session
        path = write_synthetic_session(self.root)
        self.check_all(path / "session.mcap", (
            "/robot/state", "/robot/command", "/robot/command_result",
            "/operator/decision", "/session/fault", "/session/note",
            "/camera/cam0/image", "/camera/cam0/detections"))

    def test_simulated_bench_rehearsal_validates(self):
        labels = ["--robot-id", "arm", "--arm-label", "a", "--controller-label", "c",
                  "--driver", "none", "--operator", "t"]
        bench = subprocess.run(
            [sys.executable, str(REPO_ROOT / "examples" / "bench_joint.py"),
             "--output", str(self.root / "bench.jsonl"), *labels,
             "--start-pose-note", "desk", "--joint", "base", "--delta", "1",
             "--simulate", "--acknowledge-supervised-motion"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
            input="n\ng\nHOME\ny\ng\nok\nHOME_OK\nMOVE\ny\ng\nleft\nnone\nnone\nnone\nn\ng\n")
        self.assertEqual(bench.returncode, 0, bench.stderr)
        [session] = [p for p in (self.root / "sessions").iterdir() if p.is_dir()]
        messages = self.check_all(session / "session.mcap", (
            "/robot/state", "/robot/command", "/robot/command_result",
            "/operator/decision", "/session/note"))
        for topic, _, state in messages:
            if topic == "/robot/state":
                self.assertEqual(sorted(state["signed_encoder_counts"]),
                                 sorted(state["encoder_counts"]))

    def test_simulated_robot_state_with_raw_packet_validates(self):
        from scorbot import SimulatedScorbot
        from scorbot.session.record import SessionWriter
        with SimulatedScorbot(log_path=self.root / "events.jsonl") as robot, \
                SessionWriter.create(self.root, data_source="simulated",
                                     robot_id="arm") as rec:
            rec.log_state(robot.get_state(), raw_packet=b"\x00\x01\x02")
            path = rec.path
        messages = self.check_all(path / "session.mcap", ("/robot/state",))
        self.assertEqual(messages[0][2]["raw_packet_hex"], "000102")

    def test_bad_payloads_are_rejected(self):
        from scorbot.session import schemas
        schema = json.loads(schemas.schema_json("scorbot.RobotState"))
        good = {"encoder_counts": {"base": 1}, "home_switch_bits": 0, "connected": True}
        self.assertEqual(validate(good, schema), [])
        self.assertTrue(validate({**good, "encoder_counts": {"base": "1"}}, schema))
        self.assertTrue(validate({**good, "connected": 1}, schema))
        result = json.loads(schemas.schema_json("scorbot.CommandResult"))
        self.assertTrue(validate({"command_id": "c", "status": "bogus"}, result))


if __name__ == "__main__":
    unittest.main()
