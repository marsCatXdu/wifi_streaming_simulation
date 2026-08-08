#!/usr/bin/env python3
"""Tests for target-stream WMM configuration and runner translation."""

from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from run_experiments import cli_arguments  # noqa: E402
from validate_outputs import ValidationError, _validate_wmm_config  # noqa: E402


def config(mode: str, topology: str = "dual_interface") -> dict:
    profiles = {
        "off": (0, 0, "AC_BE"),
        "on": (160, 5, "AC_VI"),
    }
    tos, tid, access_category = profiles[mode]
    return {
        "topology": topology,
        "wifi": {
            "wmm_mode": mode,
            "stream_ip_tos": tos,
            "stream_tid": tid,
            "access_category": access_category,
            "tid_to_link_mapping_ul": (
                f"{tid} 0,1" if topology in {"mlo_str", "mlo_emlsr"}
                else "not_applicable"
            ),
        },
    }


class TargetWmmModeTest(unittest.TestCase):
    def test_accepts_off_and_on_profiles(self) -> None:
        self.assertEqual(_validate_wmm_config(config("off"), "test"), "off")
        self.assertEqual(_validate_wmm_config(config("on"), "test"), "on")
        self.assertEqual(_validate_wmm_config(config("on", "mlo_str"), "test"), "on")

    def test_accepts_legacy_best_effort_profile(self) -> None:
        legacy = {
            "topology": "dual_interface",
            "wifi": {
                "access_category": "AC_BE",
                "tid_to_link_mapping_ul": "not_applicable",
            },
        }
        self.assertEqual(_validate_wmm_config(legacy, "legacy"), "off")

    def test_rejects_incoherent_marking(self) -> None:
        invalid = config("on")
        invalid["wifi"]["stream_tid"] = 0
        with self.assertRaisesRegex(ValidationError, "WMM stream profile differs"):
            _validate_wmm_config(invalid, "test")

    def test_rejects_incoherent_mlo_mapping(self) -> None:
        invalid = config("on", "mlo_str")
        invalid["wifi"]["tid_to_link_mapping_ul"] = "0 0,1"
        with self.assertRaisesRegex(ValidationError, "TID-to-link mapping differs"):
            _validate_wmm_config(invalid, "test")

    def test_runner_translates_wmm_mode(self) -> None:
        self.assertEqual(
            cli_arguments({"wifi": {"wmm_mode": "on"}}, ROOT),
            ["--wmmMode=on"],
        )


if __name__ == "__main__":
    unittest.main()
