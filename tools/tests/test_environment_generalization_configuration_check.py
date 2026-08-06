#!/usr/bin/env python3
"""Tests for the held-out qualification configuration preflight."""

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_environment_generalization_qualification_configurations as checker  # noqa: E402


class EnvironmentGeneralizationConfigurationCheckTest(unittest.TestCase):
    def test_exact_unique_scenario_arm_matrix_is_checked(self) -> None:
        checks = checker.build_checks()
        self.assertEqual(len(checks), 144)
        self.assertEqual(
            Counter(check["config"]["policy"] for check in checks),
            {
                "fixed_link_0": 48,
                "paired_value_duplication_t2": 48,
                "distributional_shadow_duplication_t2": 48,
            },
        )
        for check in checks:
            profile = check["config"].get("prediction", {}).get(
                "paired_temporal_t2_frame_profile"
            )
            if check["config"]["policy"] == "fixed_link_0":
                self.assertIsNone(profile)
            else:
                self.assertEqual(profile, "environment_generalization_v1")

    def test_direct_check_uses_validation_only_mode(self) -> None:
        check = checker.build_checks()[0]
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(checker.subprocess, "run", return_value=completed) as run:
            checker._run_check(Path("/tmp/streaming-experiment"), check)
        command = run.call_args.args[0]
        self.assertIn("--configurationCheckOnly=1", command)
        self.assertFalse(any(argument.startswith("--outputDir=") for argument in command))


if __name__ == "__main__":
    unittest.main()
