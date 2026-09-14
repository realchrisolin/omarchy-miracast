#!/usr/bin/env python3
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_privacy import (  # noqa: E402
    guess_manufacturer,
    guess_model_hint,
    sanitize_display_name,
    sanitize_private_run,
)


class BenchmarkPrivacyTest(unittest.TestCase):
    def test_sanitize_strips_mac_suffix(self):
        self.assertEqual(sanitize_display_name("hotyeah-AABB12_P2P"), "hotyeah")
        self.assertIsNone(sanitize_display_name("aa:bb:cc:dd:ee:ff"))

    def test_guess_lg(self):
        self.assertEqual(guess_manufacturer("[LG]"), "LG")
        self.assertIsNone(guess_model_hint("[LG]", "LG"))

    def test_guess_hotyeah(self):
        self.assertEqual(guess_manufacturer("hotyeah-AABB12_P2P"), "hotyeah")
        self.assertEqual(guess_model_hint("hotyeah-AABB12_P2P", "hotyeah"), None)

    def test_sanitize_private_omits_mac(self):
        pub = sanitize_private_run(
            {
                "kind": "live_session",
                "ts": "2026-09-14T00:00:00+00:00",
                "stem": "t",
                "sink": {
                    "display_name": "[LG]",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "manufacturer": "LG",
                },
                "host": {"cpu": "Test CPU", "wifi": {"driver": "iwlwifi", "sta_ssid": "Secret"}},
                "session": {"mode": "extend", "stream_mode": "1920x1080p30", "capture_path": "dmabuf"},
                "metrics": {"hyprland_pct_one_core": 5.8, "sta_channel": 44, "p2p_channel": 149},
            }
        )
        blob = str(pub)
        self.assertNotIn("aa:bb:cc", blob)
        self.assertNotIn("Secret", blob)
        self.assertEqual(pub["sink"]["manufacturer"], "LG")
        self.assertEqual(pub["metrics"]["hyprland_pct_one_core"], 5.8)

    def test_fingerprint_name_parse(self):
        from fingerprint_miracast_sink import parse_device_name, parse_wps_primary_device_type

        parsed = parse_device_name("[LG] webOS TV SM8600PUA")
        self.assertEqual(parsed.get("bracket_brand"), "LG")
        self.assertEqual(parsed.get("model_code"), "SM8600PUA")
        self.assertEqual(parsed.get("product_line"), "webOS TV")
        pri = parse_wps_primary_device_type("7-0050F204-1")
        self.assertEqual(pri.get("category"), "Display")


if __name__ == "__main__":
    unittest.main()
