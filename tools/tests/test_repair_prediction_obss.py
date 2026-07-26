from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from repair_prediction_obss import (
    POLICIES,
    REPAIR_PREFIX,
    ROUND_MARKER,
    cleanup_repair_roots,
    completed_policy_seeds,
    run_repair_loop,
    scenario_templates,
    semantic_scenario,
    write_round_config,
)
from run_experiments import expand_config, load_yaml


class ObssRepairLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.config_dir = self.base / "configs"
        self.results_dir = self.base / "results"
        self.config_dir.mkdir()
        self.results_dir.mkdir()
        shutil.copyfile(
            ROOT / "experiments/configs/prediction_obss.yaml",
            self.config_dir / "prediction_obss.yaml",
        )
        shutil.copyfile(
            ROOT / "experiments/configs/prediction_obss_repair_v1.yaml",
            self.config_dir / "prediction_obss_repair_v1.yaml",
        )
        self.templates, self.identities = scenario_templates(
            self.config_dir / "prediction_obss.yaml"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_run(
        self,
        root: Path,
        name: str,
        spec: dict,
        commit: str,
    ) -> None:
        run_dir = root / name
        run_dir.mkdir(parents=True, exist_ok=True)
        resolved = copy.deepcopy(spec["config"])
        resolved.update({"seed": spec["seed"], "run": spec["run"]})
        (run_dir / "resolved_config.json").write_text(
            json.dumps(resolved), encoding="utf-8"
        )
        (run_dir / "build_info.json").write_text(
            json.dumps({"project_git_commit": commit}), encoding="utf-8"
        )

    def _complete_specs(self, config_path: Path, commit: str = "current") -> None:
        document = load_yaml(config_path)
        root = self.results_dir / Path(document["output_root"]).name
        for index, spec in enumerate(expand_config(document)):
            self._write_run(root, f"run-{index}", spec, commit)

    def test_exact_scenario_identity_rejects_modified_configuration(self) -> None:
        specs = expand_config(load_yaml(self.config_dir / "prediction_obss.yaml"))
        observed = {
            semantic_scenario(spec["config"], self.identities) for spec in specs
        }
        self.assertEqual(
            observed, {"obss_only", "obss_plus_legacy_mixed8"}
        )

        changed = copy.deepcopy(specs[0]["config"])
        changed["obss"]["obss_stations_per_bss"] = 99
        self.assertIsNone(semantic_scenario(changed, self.identities))

    def test_collection_never_matches_links_across_commits(self) -> None:
        specs = expand_config(load_yaml(self.config_dir / "prediction_obss.yaml"))
        by_policy = {
            spec["config"]["policy"]: spec
            for spec in specs
            if spec["seed"] == 401
            and semantic_scenario(spec["config"], self.identities) == "obss_only"
        }
        root = self.results_dir / "prediction_obss"
        self._write_run(root, "link0", by_policy["fixed_link_0"], "commit-a")
        self._write_run(root, "link1", by_policy["fixed_link_1"], "commit-b")

        completed = completed_policy_seeds(
            [root], self.identities, validator=lambda path: {}
        )
        self.assertFalse(
            any(policies == POLICIES for policies in completed["obss_only"].values())
        )

        self._write_run(root, "link1-same", by_policy["fixed_link_1"], "commit-a")
        completed = completed_policy_seeds(
            [root], self.identities, validator=lambda path: {}
        )
        self.assertEqual(
            sum(
                policies == POLICIES
                for policies in completed["obss_only"].values()
            ),
            1,
        )

    def test_generation_is_exact_paired_and_duplicate_free(self) -> None:
        path = self.config_dir / f"{REPAIR_PREFIX}_round2.yaml"
        plan = {
            "obss_only": [425, 426],
            "obss_plus_legacy_mixed8": [500],
        }
        count = write_round_config(
            path, self.templates, self.identities, plan, 2
        )
        document = load_yaml(path)
        specs = expand_config(document)

        self.assertEqual(count, 6)
        self.assertEqual(document["workers"], 10)
        self.assertEqual(
            {
                (
                    semantic_scenario(spec["config"], self.identities),
                    spec["seed"],
                    spec["config"]["policy"],
                )
                for spec in specs
            },
            {
                (scenario, seed, policy)
                for scenario, seeds in plan.items()
                for seed in seeds
                for policy in POLICIES
            },
        )

    def test_cleanup_is_limited_to_direct_hidden_attempt_directories(self) -> None:
        repair = self.results_dir / REPAIR_PREFIX
        direct = repair / ".run.attempt-1"
        nested = repair / "kept" / ".run.attempt-2"
        visible = repair / "run.attempt-3"
        unrelated = repair / ".cache"
        original = self.results_dir / "prediction_obss" / ".run.attempt-4"
        for path in (direct, nested, visible, unrelated, original):
            path.mkdir(parents=True)

        self.assertEqual(cleanup_repair_roots([repair, original.parent]), 1)
        self.assertFalse(direct.exists())
        for path in (nested, visible, unrelated, original):
            self.assertTrue(path.exists())

    def test_loop_uses_fresh_seeds_resumes_failures_and_cleans_attempts(self) -> None:
        attempt = self.results_dir / REPAIR_PREFIX / ".partial.attempt-7"
        attempt.mkdir(parents=True)
        original_spec = next(
            spec
            for spec in expand_config(
                load_yaml(self.config_dir / "prediction_obss.yaml")
            )
            if semantic_scenario(spec["config"], self.identities) == "obss_only"
        )
        attempted = copy.deepcopy(original_spec["config"])
        attempted.update({"seed": 500, "run": 1})
        (attempt / "resolved_config.json").write_text(
            json.dumps(attempted), encoding="utf-8"
        )
        launched: list[Path] = []

        def runner(config_path: Path) -> int:
            launched.append(config_path)
            self._complete_specs(config_path)
            return 1

        status = run_repair_loop(
            config_dir=self.config_dir,
            results_dir=self.results_dir,
            target_groups=1,
            max_rounds=2,
            runner=runner,
            validator=lambda path: {},
        )

        self.assertEqual(status, 0)
        self.assertEqual(len(launched), 1)
        self.assertFalse(attempt.exists())
        specs = expand_config(load_yaml(launched[0]))
        seeds = {
            scenario: {
                spec["seed"]
                for spec in specs
                if semantic_scenario(spec["config"], self.identities) == scenario
            }
            for scenario in self.templates
        }
        self.assertEqual(seeds["obss_only"], {501})
        self.assertEqual(seeds["obss_plus_legacy_mixed8"], {425})
        result_root = self.results_dir / f"{REPAIR_PREFIX}_round2"
        self.assertTrue((result_root / ROUND_MARKER).is_file())

    def test_unmarked_generated_round_is_resumed_after_interruption(self) -> None:
        pending = self.config_dir / f"{REPAIR_PREFIX}_round2.yaml"
        write_round_config(
            pending,
            self.templates,
            self.identities,
            {scenario: [425] for scenario in self.templates},
            2,
        )
        launched: list[Path] = []

        def runner(config_path: Path) -> int:
            launched.append(config_path)
            self._complete_specs(config_path)
            return 0

        status = run_repair_loop(
            config_dir=self.config_dir,
            results_dir=self.results_dir,
            target_groups=1,
            runner=runner,
            validator=lambda path: {},
        )

        self.assertEqual(status, 0)
        self.assertEqual(launched, [pending])
        self.assertFalse(
            (self.config_dir / f"{REPAIR_PREFIX}_round3.yaml").exists()
        )

    def test_no_progress_stops_with_nonzero_status_after_two_rounds(self) -> None:
        launched: list[Path] = []

        def runner(config_path: Path) -> int:
            launched.append(config_path)
            return 1

        status = run_repair_loop(
            config_dir=self.config_dir,
            results_dir=self.results_dir,
            target_groups=1,
            max_rounds=10,
            max_no_progress_rounds=2,
            runner=runner,
            validator=lambda path: {},
        )

        self.assertEqual(status, 3)
        self.assertEqual(
            [path.name for path in launched],
            [
                f"{REPAIR_PREFIX}_round2.yaml",
                f"{REPAIR_PREFIX}_round3.yaml",
            ],
        )
        first = {spec["seed"] for spec in expand_config(load_yaml(launched[0]))}
        second = {spec["seed"] for spec in expand_config(load_yaml(launched[1]))}
        self.assertTrue(first.isdisjoint(second))


if __name__ == "__main__":
    unittest.main()
