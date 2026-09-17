#!/usr/bin/env python3
"""Unit tests for verify_p2p_air_band conclude()."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "verify_p2p_air_band.py"


def _load():
    import sys

    spec = importlib.util.spec_from_file_location("verify_p2p_air_band", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class ConcludeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_oper_5ghz_over_listen(self):
        st = {
            "phase": "streaming",
            "linkUp": True,
            "staFreqMHz": 5220,
            "p2pListenFreqMHz": 2437,
            "p2pOperFreqMHz": 5220,
            "p2pFreqMHz": 5220,
            "p2pFreqSource": "peer_oper",
            "radioMcc": False,
        }
        out = self.mod.conclude(st, iw_freq=5220)
        self.assertEqual(out["conclusion"], "oper_5ghz")
        self.assertEqual(out["airBand"], "5")
        self.assertTrue(any("discovery-only" in n for n in out["notes"]))

    def test_listen_fallback(self):
        st = {
            "p2pListenFreqMHz": 2437,
            "p2pOperFreqMHz": 2437,
            "p2pFreqMHz": 2437,
            "p2pFreqSource": "peer_listen",
            "radioMcc": True,
        }
        out = self.mod.conclude(st, iw_freq=5220)
        self.assertEqual(out["conclusion"], "oper_2.4")
        self.assertEqual(out["airBand"], "2.4")

    def test_inconclusive_idle(self):
        out = self.mod.conclude({}, iw_freq=None)
        self.assertEqual(out["conclusion"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
