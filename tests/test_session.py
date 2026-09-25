import json
import unittest


class SchemaTests(unittest.TestCase):
    def test_every_scorbot_schema_lists_its_required_keys(self):
        from scorbot.session import schemas
        for name, keys in schemas.REQUIRED.items():
            schema = json.loads(schemas.schema_json(name))
            self.assertEqual(schema["title"], name)
            for key in keys:
                self.assertIn(key, schema["required"])

    def test_foxglove_image_schema_is_the_vendored_official_one(self):
        from scorbot.session import schemas
        schema = json.loads(schemas.schema_json("foxglove.CompressedImage"))
        self.assertEqual(schema["required"], ["timestamp", "frame_id", "data", "format"])
        self.assertEqual(schema["properties"]["data"]["contentEncoding"], "base64")

    def test_topic_to_schema_mapping(self):
        from scorbot.session.schemas import schema_name_for_topic
        self.assertEqual(schema_name_for_topic("/robot/state"), "scorbot.RobotState")
        self.assertEqual(schema_name_for_topic("/camera/cam0/image"), "foxglove.CompressedImage")
        self.assertEqual(schema_name_for_topic("/camera/cam0/detections"), "scorbot.Detection")
        self.assertIsNone(schema_name_for_topic("/unknown"))
        self.assertIsNone(schema_name_for_topic("/camera//image"))


if __name__ == "__main__":
    unittest.main()
