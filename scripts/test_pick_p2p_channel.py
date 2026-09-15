#!/usr/bin/env python3
"""Unit tests for pick-p2p-channel quiet / quietest policy.

“Quiet” is a score threshold; ranking uses the numeric interference score.
On-device Miracast TX retries have followed that ranking (see README § P2P
channel). These tests cover the policy helpers, not live RF.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "pick-p2p-channel.py"


def _load():
    import sys

    spec = importlib.util.spec_from_file_location("pick_p2p_channel", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# Synthetic SSIDs only — no real home/device names. Channels/signals are fixtures.
SAMPLE_NMCLI = """\
 :DIRECT-sink:2:2417 MHz:90
 :Busy24:2:2417 MHz:80
*:HomeLab-5GHz:44:5220 MHz:45
 :Neighbor-5GHz:149:5745 MHz:49
 :Other149:149:5745 MHz:42
 :Weak157:157:5785 MHz:20
 :Guest24:11:2462 MHz:12
"""


class PickP2PChannelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_parse_nmcli_and_sta(self):
        sightings = self.mod.parse_nmcli_wifi_list(SAMPLE_NMCLI)
        self.assertGreaterEqual(len(sightings), 5)
        self.assertEqual(self.mod.sta_channel_from_sightings(sightings), 44)

    def test_prefers_quiet_over_busy(self):
        sightings = self.mod.parse_nmcli_wifi_list(SAMPLE_NMCLI)
        result = self.mod.pick_channel(
            sightings,
            candidates=(36, 40, 44, 48, 149, 153, 157, 161),
            sta_width_mhz=80,
        )
        # STA on 44 @80MHz occupies 36-48; 149 busy → prefer off-block UNII-3
        # even if some UNII-1 channels look "quieter" on paper.
        self.assertNotIn(result.channel, (36, 40, 44, 48, 149))
        self.assertIn(result.channel, (153, 157, 161))
        self.assertIn("avoided block", result.reason)

    def test_prefers_off_block_even_among_quiet(self):
        # All UNII-1 and UNII-3 empty/quiet; STA on 44 @80 → must leave 36-48.
        text = """\
*:HomeLab-5GHz:44:5220 MHz:45
"""
        sightings = self.mod.parse_nmcli_wifi_list(text)
        result = self.mod.pick_channel(
            sightings,
            candidates=(36, 40, 44, 48, 149, 153, 157, 161),
            sta_width_mhz=80,
        )
        self.assertTrue(result.quiet)
        self.assertIn(result.channel, (149, 153, 157, 161))
        self.assertEqual(result.sta_block, [36, 40, 44, 48])

    def test_falls_back_to_quietest_when_none_quiet(self):
        # Every candidate has an AP; none meet quiet threshold.
        text = """\
*:A:36:5180 MHz:80
 :B:40:5200 MHz:70
 :C:44:5220 MHz:60
 :D:48:5240 MHz:50
 :E:149:5745 MHz:40
 :F:153:5765 MHz:30
 :G:157:5785 MHz:25
 :H:161:5785 MHz:20
"""
        # Fix last freq for 161
        text = text.replace(":H:161:5785 MHz:20", ":H:161:5805 MHz:20")
        sightings = self.mod.parse_nmcli_wifi_list(text)
        result = self.mod.pick_channel(
            sightings,
            candidates=(36, 40, 44, 48, 149, 153, 157, 161),
            quiet_threshold=0.0,  # force "none quiet"
        )
        self.assertFalse(result.quiet)
        # Weakest / fewest → 161 (signal 20)
        self.assertEqual(result.channel, 161)
        self.assertIn("no quiet channels", result.reason)
        self.assertIn("quietest", result.reason)

    def test_reg_class_mapping(self):
        self.assertEqual(self.mod.reg_class_for_channel(6), 81)
        self.assertEqual(self.mod.reg_class_for_channel(44), 115)
        self.assertEqual(self.mod.reg_class_for_channel(149), 125)

    def test_sta_80mhz_block_unii1(self):
        self.assertEqual(self.mod.sta_80mhz_block(44), (36, 40, 44, 48))
        self.assertEqual(
            self.mod.sta_occupied_channels(44, 80), {36, 40, 44, 48}
        )
        self.assertEqual(self.mod.sta_occupied_channels(44, 20), {44})

    def test_off_block_beats_quieter_in_block(self):
        # Empty spectrum except STA; off-block must win over in-block quiet.
        text = "*:HomeLab-5GHz:44:5220 MHz:50\n"
        sightings = self.mod.parse_nmcli_wifi_list(text)
        result = self.mod.pick_channel(
            sightings,
            candidates=(36, 40, 44, 48, 149, 153, 157, 161),
            sta_width_mhz=80,
        )
        self.assertGreaterEqual(result.channel, 149)

    def test_escaped_colon_in_ssid(self):
        text = r" :Foo\:Bar:36:5180 MHz:10"
        sightings = self.mod.parse_nmcli_wifi_list(text)
        self.assertEqual(len(sightings), 1)
        self.assertEqual(sightings[0].ssid, "Foo:Bar")
        self.assertEqual(sightings[0].channel, 36)


if __name__ == "__main__":
    unittest.main()
