import base64
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "plugins" / "image-generation"
SKILL_PATH = SKILL_DIR / "SKILL.md"
SCRIPT_PATH = SKILL_DIR / "scripts" / "generate.py"
MARKETPLACE_PATH = REPO_ROOT / "marketplace.json"


def load_generate_module():
    spec = importlib.util.spec_from_file_location("marketplace_image_generation", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load image-generation script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestImageGenerationSkill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_generate_module()

    def test_skill_requires_generation_and_presentation_proof(self):
        content = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("Do not call `image_search` for a generation request", content)
        self.assertIn("Successfully generated image to <absolute path>", content)
        self.assertIn("Call `present_files`", content)
        self.assertIn("Successfully presented files", content)
        self.assertIn("only after `present_files`", content)
        self.assertIn("Run the generation command exactly once", content)
        self.assertIn("do not run the command again in the same turn", content)

    def test_marketplace_uses_dfcode_gateway_credentials(self):
        marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
        entry = next(
            plugin
            for plugin in marketplace["plugins"]
            if plugin["name"] == "image-generation"
        )

        self.assertEqual(entry["version"], "4.0.4")
        self.assertEqual(entry["required_env"], ["IMAGE_GATEWAY_KEY"])
        self.assertEqual(entry["optional_env"], ["IMAGE_GATEWAY_BASE_URL"])
        self.assertEqual(entry["credentials"][0]["key"], "IMAGE_GATEWAY_KEY")

    def test_skill_and_marketplace_lock_generation_to_gpt_image_2(self):
        skill_content = SKILL_PATH.read_text(encoding="utf-8")
        script_content = SCRIPT_PATH.read_text(encoding="utf-8")
        marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
        entry = next(
            plugin
            for plugin in marketplace["plugins"]
            if plugin["name"] == "image-generation"
        )

        self.assertIn("fixed to `gpt-image-2`", skill_content)
        self.assertIn('IMAGE_MODEL = "gpt-image-2"', script_content)
        self.assertNotIn("IMAGE_GENERATION_MODEL", skill_content)
        self.assertNotIn("IMAGE_GENERATION_MODEL", script_content)
        self.assertNotIn("IMAGE_GENERATION_MODEL", entry["optional_env"])
        self.assertNotIn("grok-imagine", skill_content)
        self.assertNotIn("grok-imagine", script_content)
        self.assertNotIn("grok-imagine", json.dumps(entry))

    def test_script_has_no_third_party_runtime_dependency(self):
        content = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertNotIn("import requests", content)
        self.assertNotIn("from dotenv", content)
        self.assertIn("import urllib.request", content)

    def test_read_timeout_exceeds_gateway_upstream_timeout(self):
        connect_timeout, read_timeout = self.module.REQUEST_TIMEOUT

        self.assertEqual(connect_timeout, 10)
        self.assertEqual(read_timeout, 330)
        self.assertGreater(read_timeout, 300)

    def test_default_configuration_targets_shared_gateway(self):
        with patch.dict(os.environ, {"IMAGE_GATEWAY_KEY": "test-key"}, clear=True):
            self.assertEqual(
                self.module._load_config(),
                (
                    "test-key",
                    "http://221.0.79.252:18120/v1",
                ),
            )

    def test_model_environment_override_is_ignored(self):
        with patch.dict(
            os.environ,
            {
                "IMAGE_GATEWAY_KEY": "test-key",
                "IMAGE_GENERATION_MODEL": "grok-imagine-image-quality",
            },
            clear=True,
        ):
            self.assertEqual(
                self.module._load_config(),
                ("test-key", "http://221.0.79.252:18120/v1"),
            )

    def test_reference_images_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not support image editing"):
            self.module.generate_image(
                "prompt.txt",
                ["reference.png"],
                "output.jpg",
                "1:1",
            )

    def test_generation_writes_non_empty_output_and_returns_absolute_path(self):
        png_bytes = b"\x89PNG\r\n\x1a\ngenerated-png"
        response_body = json.dumps(
            {"data": [{"b64_json": base64.b64encode(png_bytes).decode("ascii")}]}
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.txt"
            output_path = Path(temp_dir) / "generated.png"
            prompt_path.write_text("A blue circle on a white background", encoding="utf-8")

            with (
                patch.dict(os.environ, {"IMAGE_GATEWAY_KEY": "test-key"}, clear=True),
                patch.object(
                    self.module, "_post_generation", return_value=response_body
                ) as post_generation,
            ):
                result = self.module.generate_image(
                    str(prompt_path),
                    [],
                    str(output_path),
                    "1:1",
                )

            post_generation.assert_called_once_with(
                "http://221.0.79.252:18120/v1",
                "test-key",
                "gpt-image-2",
                "A blue circle on a white background",
                "1024x1024",
            )
            self.assertEqual(output_path.read_bytes(), png_bytes)
            self.assertEqual(
                result,
                f"Successfully generated image to {output_path.resolve()}",
            )

    def test_prompt_length_is_bounded_before_network_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.txt"
            output_path = Path(temp_dir) / "generated.png"
            prompt_path.write_text("x" * 10_001, encoding="utf-8")

            with patch.dict(os.environ, {"IMAGE_GATEWAY_KEY": "test-key"}, clear=True):
                with self.assertRaisesRegex(ValueError, "exceeds the maximum length"):
                    self.module.generate_image(
                        str(prompt_path),
                        [],
                        str(output_path),
                        "1:1",
                    )


if __name__ == "__main__":
    unittest.main()
