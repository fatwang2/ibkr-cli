import tempfile
import unittest
from pathlib import Path

from ibkr_cli.config import default_config, get_profile, load_config, save_config, serialize_config


class ConfigTests(unittest.TestCase):
    def test_default_config_contains_expected_profiles(self) -> None:
        config = default_config()

        self.assertEqual(config.default_profile, "paper")
        self.assertTrue({"paper", "live", "gateway-paper", "gateway-live"} <= set(config.profiles))
        self.assertEqual(config.profiles["gateway-paper"].port, 4002)

    def test_config_round_trip(self) -> None:
        source = default_config()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            saved = save_config(source, path=path)
            loaded, exists = load_config(saved)

        self.assertTrue(exists)
        self.assertEqual(loaded.default_profile, source.default_profile)
        self.assertEqual(loaded.profiles["paper"].port, source.profiles["paper"].port)
        self.assertEqual(loaded.profiles["gateway-live"].mode, "live")

    def test_serialize_config_is_toml_like(self) -> None:
        text = serialize_config(default_config())

        self.assertIn('default_profile = "paper"', text)
        self.assertIn("[profiles.gateway-paper]", text)
        self.assertIn("port = 4002", text)

    def test_get_profile_uses_default_when_name_missing(self) -> None:
        config = default_config()
        name, profile = get_profile(config)

        self.assertEqual(name, "paper")
        self.assertEqual(profile.port, 7497)
