#!/usr/bin/env python3
"""Unit tests for list_p2p_radios resolve scoring and adapter naming."""

from __future__ import annotations

import unittest
from unittest import mock

from list_p2p_radios import _adapter_name, resolve_iface


class ResolveIfaceTest(unittest.TestCase):
    def test_prefers_idle_p2p_go(self):
        radios = [
            {"iface": "wlp0s20f3", "p2pGo": True, "inUse": True},
            {"iface": "wlan1", "p2pGo": True, "inUse": False},
            {"iface": "wlan0", "p2pGo": False, "inUse": False},
        ]
        self.assertEqual(resolve_iface("auto", radios), "wlan1")

    def test_idle_beats_busy_even_when_name_worse(self):
        # Strong idle preference (score weight 2) over wlp* name bonus.
        radios = [
            {"iface": "wlp0s20f3", "p2pGo": True, "inUse": True},
            {"iface": "wlx00aabbccddee", "p2pGo": True, "inUse": False},
        ]
        self.assertEqual(resolve_iface("auto", radios), "wlx00aabbccddee")

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


class AdapterNameTest(unittest.TestCase):
    def _patch_uevent(self, uevent: str):
        real_read = __import__("pathlib").Path.read_text

        def read_text(self, *a, **k):
            if str(self).endswith("uevent"):
                return uevent
            raise OSError("nope")

        return mock.patch.object(__import__("pathlib").Path, "read_text", read_text)

    def test_pci_name_keeps_full_vendor_string(self):
        lspci = (
            "00:14.3 Network controller [0280]: "
            "Intel Corporation Wi-Fi 6 AX201 [8086:a0f0] (rev 20)\n"
        )

        def fake_run(cmd, timeout=4.0):
            class R:
                stdout = lspci
                returncode = 0

            return R()

        with mock.patch("list_p2p_radios._run", side_effect=fake_run):
            with mock.patch("list_p2p_radios.shutil.which", return_value="/usr/bin/lspci"):
                with self._patch_uevent("DRIVER=iwlwifi\nPCI_ID=8086:A0F0\n"):
                    name = _adapter_name("phy0", "iwlwifi")
        self.assertEqual(name, "Intel Corporation Wi-Fi 6 AX201")

    def test_pci_other_vendor_no_hardcoded_rewrite(self):
        lspci = (
            "01:00.0 Network controller [0280]: "
            "Some Vendor Ltd. Fancy Card [10ec:1234] (rev 01)\n"
        )

        def fake_run(cmd, timeout=4.0):
            class R:
                stdout = lspci
                returncode = 0

            return R()

        with mock.patch("list_p2p_radios._run", side_effect=fake_run):
            with mock.patch("list_p2p_radios.shutil.which", return_value="/usr/bin/lspci"):
                with self._patch_uevent("DRIVER=rtw88\nPCI_ID=10EC:1234\n"):
                    name = _adapter_name("phy1", "rtw88")
        self.assertEqual(name, "Some Vendor Ltd. Fancy Card")


if __name__ == "__main__":
    unittest.main()
