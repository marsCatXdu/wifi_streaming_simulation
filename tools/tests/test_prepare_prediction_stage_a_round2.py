from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from prepare_prediction_stage_a_round2 import (
    POLICIES,
    cleanup_failed_attempts,
    completed_policy_seeds,
    condition_templates,
    configured_seeds,
    plan_fresh_seeds,
    semantic_condition,
    write_round2_config,
)
from run_experiments import expand_config, load_yaml


class StageARound2RepairTests(unittest.TestCase):
    def test_plans_fresh_paired_seeds_and_writes_unique_matrix(self) -> None:
        source = ROOT / "experiments/configs/prediction_stage_a.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            stage_a = config_dir / "prediction_stage_a.yaml"
            shutil.copyfile(source, stage_a)
            output = config_dir / "prediction_stage_a_repair_v1_round2.yaml"

            templates = condition_templates(stage_a)
            self.assertEqual(len(templates), 16)
            unloaded = [
                condition
                for condition in templates
                if condition.rate_mbps_per_station is None
            ]
            self.assertEqual(len(unloaded), 1)

            completed = defaultdict(lambda: defaultdict(set))
            incomplete_condition = sorted(templates)[0]
            for condition in templates:
                count = 8 if condition == incomplete_condition else 10
                for seed in range(1, count + 1):
                    completed[condition][(seed, 1, "commit")] = set(POLICIES)
            completed[incomplete_condition][(99, 1, "commit")] = {"fixed_link_0"}

            used = configured_seeds([stage_a])
            plan, missing = plan_fresh_seeds(templates, completed, used)
            self.assertEqual(missing, 2)
            self.assertEqual(plan, {incomplete_condition: [211, 212]})

            generated = write_round2_config(output, templates, plan)
            self.assertEqual(generated, 4)
            document = load_yaml(output)
            self.assertEqual(document["workers"], 10)
            self.assertEqual(
                document["output_root"],
                "results/prediction_stage_a_repair_v1_round2",
            )
            specs = expand_config(document)
            self.assertEqual(
                {spec["seed"] for spec in specs},
                {211, 212},
            )
            self.assertEqual(
                {spec["config"]["policy"] for spec in specs},
                POLICIES,
            )
            self.assertEqual(
                {semantic_condition(spec["config"]) for spec in specs},
                {incomplete_condition},
            )
            identities = {
                (
                    json.dumps(spec["config"], sort_keys=True),
                    spec["seed"],
                    spec["run"],
                )
                for spec in specs
            }
            self.assertEqual(len(identities), len(specs))

    def test_completed_runs_group_by_semantics_not_directory_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results = root / "results"
            results.mkdir()
            for directory, policy, commit, run_number in (
                ("arbitrary-old-id", "fixed_link_0", "old", 1),
                ("unrelated-new-id", "fixed_link_1", "new", 1),
                ("different-run-id", "fixed_link_1", "old", 2),
            ):
                run = results / directory
                run.mkdir()
                (run / "resolved_config.json").write_text(
                    json.dumps(
                        {
                            "seed": 207,
                            "run": run_number,
                            "policy": policy,
                            "background": {
                                "profile": "legacy_mixed8",
                                "traffic": "udp_bursty",
                                "rate_mbps_per_station": 5,
                                "correlation": {"mode": "common_bursts"},
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                (run / "build_info.json").write_text(
                    json.dumps({"project_git_commit": commit}),
                    encoding="utf-8",
                )

            hidden = results / ".failed.attempt-123"
            hidden.mkdir()
            (hidden / "resolved_config.json").write_text("{}", encoding="utf-8")

            completed = completed_policy_seeds(
                [results], validator=lambda path: {"valid": path.is_dir()}
            )
            self.assertEqual(len(completed), 1)
            groups = next(iter(completed.values()))
            self.assertEqual(groups[(207, 1, "old")], {"fixed_link_0"})
            self.assertEqual(groups[(207, 1, "new")], {"fixed_link_1"})
            self.assertEqual(groups[(207, 2, "old")], {"fixed_link_1"})
            self.assertFalse(any(policies == POLICIES for policies in groups.values()))

    def test_cleanup_removes_only_direct_hidden_attempt_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt_a = root / ".abc.attempt-1"
            attempt_b = root / ".def.attempt-2"
            completed = root / "completed"
            nested_attempt = completed / ".nested.attempt-3"
            ordinary_hidden = root / ".cache"
            for path in (
                attempt_a,
                attempt_b,
                completed,
                nested_attempt,
                ordinary_hidden,
            ):
                path.mkdir(exist_ok=True)
            log = root / ".ghi.stdout-1.tmp"
            log.write_text("keep", encoding="utf-8")

            self.assertEqual(cleanup_failed_attempts(root), 2)
            self.assertFalse(attempt_a.exists())
            self.assertFalse(attempt_b.exists())
            self.assertTrue(completed.is_dir())
            self.assertTrue(nested_attempt.is_dir())
            self.assertTrue(ordinary_hidden.is_dir())
            self.assertTrue(log.is_file())

    def test_no_missing_groups_produces_empty_plan(self) -> None:
        source = ROOT / "experiments/configs/prediction_stage_a.yaml"
        templates = condition_templates(source)
        completed = defaultdict(lambda: defaultdict(set))
        for condition in templates:
            for seed in range(1, 11):
                completed[condition][(seed, 1, "commit")] = set(POLICIES)
        plan, missing = plan_fresh_seeds(
            templates,
            completed,
            configured_seeds([source]),
        )
        self.assertEqual(missing, 0)
        self.assertEqual(plan, {})


if __name__ == "__main__":
    unittest.main()
