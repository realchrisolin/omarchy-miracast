#!/usr/bin/env python3
"""Unit tests for list_p2p_radios resolve scoring."""

from __future__ import annotations

import unittest

from list_p2p_radios import resolve_iface


class ResolveIfaceTest(unittest.TestCase):
    def test_prefers_idle_p2p_go(self):
        radios = [
            {"iface": "wlp0s20f3", "p2pGo": True, "inUse": True},
            {"iface": "wlan1", "p2pGo": True, "inUse": False},
            {"iface": "wlan0", "p2pGo": False, "inUse": False},
        ]
        self.assertEqual(resolve_iface("auto", radios), "wlan1")

    def test_explicit_prefer(self):
        radios = [
            {"iface": "wlp0s20f3", "p2pGo": True, "inUse": True},
            {"iface": "wlan1", "p2pGo": True, "inUse": False},
        ]
        self.assertEqual(resolve_iface("wlp0s20f3", radios), "wlp0s20f3")

    def test_fallback_to_busy_go(self):
        radios = [
            {"iface": "wlp0s20f3", "p2pGo": True, "inUse": True},
            {"iface": "wlan0", "p2pGo": False, "inUse": False},
        ]
        self.assertEqual(resolve_iface("auto", radios), "wlp0s20f3")

    def test_no_go_falls_back(self):
        radios = [
            {"iface": "wlan0", "p2pGo": False, "inUse": True},
            {"iface": "wlan1", "p2pGo": False, "inUse": False},
        ]
        self.assertEqual(resolve_iface("auto", radios), "wlan1")


if __name__ == "__main__":
    unittest.main()
