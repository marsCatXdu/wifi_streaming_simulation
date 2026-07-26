#!/usr/bin/env python3
"""Prepare a fresh-seed Stage-A repair after the first repair batch finishes."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from run_experiments import expand_config, load_yaml
from validate_outputs import validate_run

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CONFIG = ROOT / "experiments/configs/prediction_stage_a.yaml"
REPAIR_V1_CONFIG = ROOT / "experiments/configs/prediction_stage_a_repair_v1.yaml"
ORIGINAL_RESULTS = ROOT / "results/prediction_stage_a"
REPAIR_V1_RESULTS = ROOT / "results/prediction_stage_a_repair_v1"
ROUND2_CONFIG = ROOT / "experiments/configs/prediction_stage_a_repair_v1_round2.yaml"
TARGET_GROUPS = 10
FIRST_FRESH_SEED = 211
POLICIES = {"fixed_link_0", "fixed_link_1"}
GroupKey = tuple[int, int, str]


@dataclass(frozen=True, order=True)
class Condition:
    """Semantic Stage-A scenario/load coordinates."""

    correlation_mode: str
    rate_mbps_per_station: Decimal | None

    @property
    def label(self) -> str:
        """Return a stable human-readable condition label."""
        if self.rate_mbps_per_station is None:
            return "unloaded"
        return f"{self.correlation_mode}@{self.rate_mbps_per_station}"


def _background_value(background: dict[str, Any], nominal: str, resolved: str) -> Any:
    if nominal in background:
        return background[nominal]
    return background.get(resolved)


def semantic_condition(config: dict[str, Any]) -> Condition | None:
    """Map nominal or resolved configuration fields to Stage-A coordinates."""
    background = config.get("background")
    if not isinstance(background, dict):
        return None
    profile = _background_value(background, "background_profile", "profile")
    traffic = _background_value(background, "background_traffic", "traffic")
    if profile == "none" and traffic == "none":
        return Condition("unloaded", None)
    if profile != "legacy_mixed8" or traffic != "udp_bursty":
        return None
    correlation = background.get("correlation", {})
    mode = background.get("correlation_mode")
    if mode is None and isinstance(correlation, dict):
        mode = correlation.get("mode")
    rate = _background_value(
        background, "background_rate_mbps", "rate_mbps_per_station"
    )
    if not isinstance(mode, str) or not mode or rate is None:
        return None
    try:
        normalized_rate = Decimal(str(rate))
    except Exception:
        return None
    return Condition(mode, normalized_rate)


def condition_templates(
    stage_a_config: Path,
) -> dict[Condition, dict[str, Any]]:
    """Return one exact policy overlay for each original Stage-A condition."""
    document = load_yaml(stage_a_config)
    templates: dict[Condition, dict[str, Any]] = {}
    policies_by_condition: dict[Condition, set[str]] = defaultdict(set)
    for policy in document.get("policies", []):
        if not isinstance(policy, dict):
            continue
        config = policy.get("config", {})
        if not isinstance(config, dict):
            continue
        resolved = copy.deepcopy(document.get("base", {}))
        for dotted, value in config.items():
            node = resolved
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        condition = semantic_condition(resolved)
        if condition is None:
            continue
        policy_name = policy.get("name")
        if policy_name in POLICIES:
            policies_by_condition[condition].add(policy_name)
        existing = templates.setdefault(condition, copy.deepcopy(config))
        if existing != config:
            raise ValueError(
                f"policies do not preserve condition {condition.label} exactly"
            )
    for condition in templates:
        if policies_by_condition[condition] != POLICIES:
            raise ValueError(f"condition {condition.label} lacks both fixed policies")
    return templates


def configured_seeds(config_paths: Iterable[Path]) -> dict[Condition, set[int]]:
    """Collect all seeds assigned by input matrices, independent of run identity."""
    used: dict[Condition, set[int]] = defaultdict(set)
    for path in config_paths:
        for spec in expand_config(load_yaml(path)):
            condition = semantic_condition(spec["config"])
            if condition is not None:
                used[condition].add(spec["seed"])
    return used


def completed_policy_seeds(
    result_roots: Iterable[Path],
    validator: Callable[[Path], dict[str, Any]] = validate_run,
) -> dict[Condition, dict[GroupKey, set[str]]]:
    """Collect valid policies by condition, seed, run, and project commit."""
    completed: dict[Condition, dict[GroupKey, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for root in result_roots:
        if not root.is_dir():
            continue
        for run_dir in sorted(root.iterdir(), key=lambda path: path.name):
            if not run_dir.is_dir() or run_dir.name.startswith("."):
                continue
            try:
                validator(run_dir)
                config = json.loads(
                    (run_dir / "resolved_config.json").read_text(encoding="utf-8")
                )
                build = json.loads(
                    (run_dir / "build_info.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, TypeError):
                continue
            condition = semantic_condition(config)
            policy = config.get("policy")
            seed = config.get("seed")
            run = config.get("run")
            commit = build.get("project_git_commit")
            if (
                condition is not None
                and policy in POLICIES
                and isinstance(seed, int)
                and seed > 0
                and isinstance(run, int)
                and run > 0
                and isinstance(commit, str)
                and commit
            ):
                completed[condition][(seed, run, commit)].add(policy)
    return completed


def plan_fresh_seeds(
    templates: dict[Condition, dict[str, Any]],
    completed: dict[Condition, dict[GroupKey, set[str]]],
    used: dict[Condition, set[int]],
    target_groups: int = TARGET_GROUPS,
    first_fresh_seed: int = FIRST_FRESH_SEED,
) -> tuple[dict[Condition, list[int]], int]:
    """Allocate only enough unused fresh seeds to replace unmatched groups."""
    plan: dict[Condition, list[int]] = {}
    missing_total = 0
    for condition in sorted(templates):
        matched = sum(
            policies == POLICIES
            for policies in completed.get(condition, {}).values()
        )
        missing = max(0, target_groups - matched)
        missing_total += missing
        if not missing:
            continue
        seeds: list[int] = []
        condition_used = used.get(condition, set())
        candidate = max(first_fresh_seed, max(condition_used, default=0) + 1)
        while len(seeds) < missing:
            if candidate not in condition_used:
                seeds.append(candidate)
            candidate += 1
        plan[condition] = seeds
    return plan, missing_total


def round2_document(
    templates: dict[Condition, dict[str, Any]],
    plan: dict[Condition, list[int]],
) -> dict[str, Any]:
    """Build an inheritance-compatible matrix with paired policy seeds."""
    policies: list[dict[str, Any]] = []
    for condition in sorted(plan):
        for policy in sorted(POLICIES):
            policies.append(
                {
                    "name": policy,
                    "seeds": list(plan[condition]),
                    "config": copy.deepcopy(templates[condition]),
                }
            )
    return {
        "extends": "prediction_stage_a.yaml",
        "name": "prediction-stage-a-repair-v1-round2",
        "output_root": "results/prediction_stage_a_repair_v1_round2",
        "workers": 10,
        "policies": policies,
    }


def write_round2_config(
    path: Path,
    templates: dict[Condition, dict[str, Any]],
    plan: dict[Condition, list[int]],
) -> int:
    """Atomically write and verify the expanded round-two matrix."""
    document = round2_document(templates, plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        yaml.safe_dump(document, output, sort_keys=False)
        temporary = Path(output.name)
    os.replace(temporary, path)

    specs = expand_config(load_yaml(path))
    identities = {
        (
            json.dumps(spec["config"], sort_keys=True, separators=(",", ":")),
            spec["seed"],
            spec["run"],
        )
        for spec in specs
    }
    if len(identities) != len(specs):
        raise ValueError("generated round-two matrix has duplicate expanded specs")
    expected = 2 * sum(len(seeds) for seeds in plan.values())
    if len(specs) != expected:
        raise ValueError(
            f"generated {len(specs)} runs, expected {expected} paired runs"
        )
    return len(specs)


def cleanup_failed_attempts(repair_root: Path) -> int:
    """Delete only direct hidden failed-attempt directories."""
    if not repair_root.is_dir():
        return 0
    removed = 0
    for child in sorted(repair_root.iterdir(), key=lambda path: path.name):
        if (
            child.name.startswith(".")
            and ".attempt-" in child.name
            and child.is_dir()
            and not child.is_symlink()
        ):
            shutil.rmtree(child)
            removed += 1
    return removed


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-groups", type=int, default=TARGET_GROUPS, help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.target_groups < 1:
        parser.error("--target-groups must be positive")

    templates = condition_templates(ORIGINAL_CONFIG)
    if len(templates) != 16:
        raise ValueError(f"expected 16 Stage-A conditions, found {len(templates)}")
    completed = completed_policy_seeds([ORIGINAL_RESULTS, REPAIR_V1_RESULTS])
    used = configured_seeds([ORIGINAL_CONFIG, REPAIR_V1_CONFIG])
    plan, missing_total = plan_fresh_seeds(
        templates, completed, used, target_groups=args.target_groups
    )
    cleanup_count = cleanup_failed_attempts(REPAIR_V1_RESULTS)

    generated_runs = 0
    if missing_total:
        generated_runs = write_round2_config(ROUND2_CONFIG, templates, plan)

    print(f"STAGE_A_REPAIR_CLEANUP_COUNT={cleanup_count}")
    print(f"STAGE_A_REPAIR_MISSING_MATCHED_GROUP_COUNT={missing_total}")
    print(f"STAGE_A_REPAIR_GENERATED_RUN_COUNT={generated_runs}")
    print(f"STAGE_A_REPAIR_CONFIG_PATH={_display_path(ROUND2_CONFIG)}")
    if not missing_total:
        print("STAGE_A_REPAIR_NO_REPAIR=1")


if __name__ == "__main__":
    main()
