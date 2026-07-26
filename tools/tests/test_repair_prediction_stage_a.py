from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from prepare_prediction_stage_a_round2 import condition_templates
from repair_prediction_stage_a import (
    REPAIR_PREFIX,
    run_repair_loop,
    semantic_condition,
    write_round_config,
)
from run_experiments import expand_config, load_yaml


class StageARepairLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.config_dir = self.base / "configs"
        self.results_dir = self.base / "results"
        self.config_dir.mkdir()
        self.results_dir.mkdir()
        shutil.copyfile(
            ROOT / "experiments/configs/prediction_stage_a.yaml",
            self.config_dir / "prediction_stage_a.yaml",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _complete_specs(self, config_path: Path, commit: str = "current") -> None:
        document = load_yaml(config_path)
        result_root = self.results_dir / Path(document["output_root"]).name
        result_root.mkdir(parents=True, exist_ok=True)
        for index, spec in enumerate(expand_config(document)):
            run_dir = result_root / f"run-{index}"
            run_dir.mkdir(exist_ok=True)
            resolved = dict(spec["config"])
            resolved.update({"seed": spec["seed"], "run": spec["run"]})
            (run_dir / "resolved_config.json").write_text(
                json.dumps(resolved), encoding="utf-8"
            )
            (run_dir / "build_info.json").write_text(
                json.dumps({"project_git_commit": commit}), encoding="utf-8"
            )

    def test_loop_uses_seeds_above_attempts_and_tolerates_child_failures(self) -> None:
        attempt = self.results_dir / REPAIR_PREFIX / ".partial.attempt-7"
        attempt.mkdir(parents=True)
        templates = condition_templates(self.config_dir / "prediction_stage_a.yaml")
        condition = sorted(templates)[0]
        attempted = {
            "seed": 500,
            "run": 1,
            "policy": "fixed_link_0",
            "background": {
                "profile": "legacy_mixed8",
                "traffic": "udp_bursty",
                "rate_mbps_per_station": float(
                    condition.rate_mbps_per_station or 0
                ),
                "correlation": {"mode": condition.correlation_mode},
            },
        }
        if condition.rate_mbps_per_station is None:
            attempted["background"] = {"profile": "none", "traffic": "none"}
        (attempt / "resolved_config.json").write_text(
            json.dumps(attempted), encoding="utf-8"
        )

        launched: list[Path] = []

        def runner(config_path: Path) -> int:
            launched.append(config_path)
            self._complete_specs(config_path)
            return 1

        result = run_repair_loop(
            config_dir=self.config_dir,
            results_dir=self.results_dir,
            target_groups=1,
            max_rounds=2,
            runner=runner,
            validator=lambda path: {},
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(launched), 1)
        self.assertFalse(attempt.exists())
        specs = expand_config(load_yaml(launched[0]))
        seeds_for_condition = {
            spec["seed"]
            for spec in specs
            if semantic_condition(spec["config"]) == condition
        }
        self.assertEqual(seeds_for_condition, {501})
        self.assertEqual(len(specs), 32)

    def test_no_progress_is_bounded_and_each_round_is_distinct(self) -> None:
        launched: list[Path] = []

        def runner(config_path: Path) -> int:
            launched.append(config_path)
            return 1

        result = run_repair_loop(
            config_dir=self.config_dir,
            results_dir=self.results_dir,
            target_groups=1,
            max_rounds=10,
            max_no_progress_rounds=2,
            runner=runner,
            validator=lambda path: {},
        )

        self.assertEqual(result, 3)
        self.assertEqual(
            [path.name for path in launched],
            [
                f"{REPAIR_PREFIX}_round2.yaml",
                f"{REPAIR_PREFIX}_round3.yaml",
            ],
        )
        first_seeds = {
            spec["seed"] for spec in expand_config(load_yaml(launched[0]))
        }
        second_seeds = {
            spec["seed"] for spec in expand_config(load_yaml(launched[1]))
        }
        self.assertTrue(first_seeds.isdisjoint(second_seeds))

    def test_unmarked_round_is_resumed_instead_of_reallocated(self) -> None:
        templates = condition_templates(self.config_dir / "prediction_stage_a.yaml")
        plan = {condition: [211] for condition in templates}
        pending = self.config_dir / f"{REPAIR_PREFIX}_round2.yaml"
        write_round_config(pending, templates, plan, 2)
        launched: list[Path] = []

        def runner(config_path: Path) -> int:
            launched.append(config_path)
            self._complete_specs(config_path)
            return 0

        result = run_repair_loop(
            config_dir=self.config_dir,
            results_dir=self.results_dir,
            target_groups=1,
            runner=runner,
            validator=lambda path: {},
        )

        self.assertEqual(result, 0)
        self.assertEqual(launched, [pending])
        self.assertFalse(
            (self.config_dir / f"{REPAIR_PREFIX}_round3.yaml").exists()
        )


if __name__ == "__main__":
    unittest.main()
