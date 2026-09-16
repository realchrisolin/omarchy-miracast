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

    def test_parses_station_dump_without_mac(self):
        dump = """\
Station aa:bb:cc:dd:ee:ff (on p2p-wlp0s20-6)
	tx bytes:1000
	tx packets:100
	tx retries:5
	tx failed:0
	rx drop misc:2
	signal:  -38 [-38] dBm
	signal avg:-37 dBm
	tx bitrate:72.2 MBit/s MCS 7 short GI
	rx bitrate:65.0 MBit/s MCS 6
"""
        fields = self.mod.parse_station_dump(dump)
        self.assertEqual(fields["p2pSignalDbm"], -37)  # prefers signal avg
        self.assertEqual(fields["p2pTxBitrateMbps"], 72.2)
        self.assertEqual(fields["p2pRxBitrateMbps"], 65.0)
        self.assertEqual(fields["p2pTxRetries"], 5)
        self.assertEqual(fields["p2pTxPackets"], 100)
        self.assertEqual(fields["p2pTxRetryPercent"], 5.0)
        self.assertEqual(fields["p2pTxFailed"], 0)
        self.assertEqual(fields["p2pRxDropMisc"], 2)
        blob = str(fields)
        self.assertNotIn("aa:bb:cc", blob)

    def test_merge_with_station_text(self):
        dump = "signal:  -40 dBm\ntx bitrate: 144.4 MBit/s\ntx packets: 50\ntx retries: 1\n"
        out = self.mod.merge_radio_fields({}, SAMPLE_IW, station_text=dump)
        self.assertEqual(out["p2pChannel"], 161)
        self.assertEqual(out["p2pSignalDbm"], -40)
        self.assertEqual(out["p2pTxBitrateMbps"], 144.4)
        self.assertEqual(out["p2pTxRetryPercent"], 2.0)

    def test_cross_band_listen_freq_overrides_iw_lie(self):
        # Intel MCC: iw shows GO on STA's 5 GHz ch; sink listen_freq is 2.4.
        iw = """\
	Interface p2p-wlp0s20-1
		type P2P-GO
		channel 44 (5220 MHz), width: 20 MHz, center1: 5220 MHz
	Interface wlp0s20f3
		type managed
		channel 44 (5220 MHz), width: 80 MHz, center1: 5210 MHz
"""
        peer = "listen_freq=2437\noper_freq=5220\n"
        out = self.mod.merge_radio_fields({}, iw, peer_text=peer)
        self.assertTrue(out["radioMcc"])
        self.assertEqual(out["p2pFreqMHz"], 2437)
        self.assertEqual(out["p2pChannel"], 6)
        self.assertEqual(out["p2pFreqSource"], "peer_listen")
        self.assertEqual(out["staFreqMHz"], 5220)


if __name__ == "__main__":
    unittest.main()
