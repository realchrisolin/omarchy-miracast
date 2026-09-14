#!/usr/bin/env python3
"""Unit tests for attach_radio_channel_fields (iw parse / MCC flag)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "attach_radio_channel_fields.py"

# Realistic iw dump: GO then Unnamed P2P-device then managed STA.
SAMPLE_IW = """\
phy#0
	Interface p2p-wlp0s20-6
		type P2P-GO
		channel 161 (5805 MHz), width: 20 MHz (no HT), center1: 5805 MHz
	Unnamed/non-netdev interface
		type P2P-device
	Interface wlp0s20f3
		type managed
		channel 44 (5220 MHz), width: 80 MHz, center1: 5210 MHz
"""


def _load():
    import sys

    spec = importlib.util.spec_from_file_location("attach_radio_channel_fields", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class AttachRadioChannelFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_parses_sta_and_go_across_unnamed_p2p_device(self):
        fields = self.mod.parse_iw_dev(SAMPLE_IW)
        self.assertEqual(fields["staChannel"], 44)
        self.assertEqual(fields["staFreqMHz"], 5220)
        self.assertEqual(fields["staWidthMHz"], 80)
        self.assertEqual(fields["staIface"], "wlp0s20f3")
        self.assertEqual(fields["p2pChannel"], 161)
        self.assertEqual(fields["p2pFreqMHz"], 5805)
        self.assertEqual(fields["p2pWidthMHz"], 20)
        self.assertEqual(fields["p2pIface"], "p2p-wlp0s20-6")
        self.assertEqual(fields["p2pRole"], "P2P-GO")
        self.assertTrue(fields["radioMcc"])

    def test_scc_same_channel(self):
        text = """\
	Interface p2p-wlp0s20-1
		type P2P-GO
		channel 44 (5220 MHz), width: 20 MHz, center1: 5220 MHz
	Interface wlp0s20f3
		type managed
		channel 44 (5220 MHz), width: 80 MHz, center1: 5210 MHz
"""
        fields = self.mod.parse_iw_dev(text)
        self.assertEqual(fields["staChannel"], 44)
        self.assertEqual(fields["p2pChannel"], 44)
        self.assertFalse(fields["radioMcc"])

    def test_merge_clears_stale_keys(self):
        data = {"phase": "idle", "staChannel": 99, "p2pChannel": 99, "radioMcc": True}
        out = self.mod.merge_radio_fields(data, "\tInterface wlp0s20f3\n\t\ttype managed\n")
        self.assertNotIn("staChannel", out)
        self.assertNotIn("p2pChannel", out)
        self.assertNotIn("radioMcc", out)
        self.assertEqual(out["phase"], "idle")


if __name__ == "__main__":
    unittest.main()
