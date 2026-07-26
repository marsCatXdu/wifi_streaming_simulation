#!/usr/bin/env python3
"""Run bounded, resumable OOD repair rounds until both scenarios are complete."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from run_experiments import expand_config, load_yaml
from validate_outputs import validate_run

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "experiments/configs"
ORIGINAL_CONFIG = CONFIG_DIR / "prediction_obss.yaml"
ORIGINAL_RESULTS = ROOT / "results/prediction_obss"
REPAIR_PREFIX = "prediction_obss_repair_v1"
POLICIES = {"fixed_link_0", "fixed_link_1"}
SCENARIOS = {"obss_only", "obss_plus_legacy_mixed8"}
TARGET_GROUPS = 24
MAX_ROUNDS = 10
MAX_NO_PROGRESS_ROUNDS = 2
FIRST_FRESH_SEED = 425
ROUND_PATTERN = re.compile(rf"^{REPAIR_PREFIX}_round([1-9][0-9]*)\.yaml$")
ROUND_MARKER = ".obss_repair_round.json"
GroupKey = tuple[int, int, str]


def emit(event: str, **values: object) -> None:
    """Print one stable, immediately visible progress sentinel."""
    fields = " ".join(f"{key}={value}" for key, value in values.items())
    print(f"OOD_REPAIR_{event}" + (f" {fields}" if fields else ""), flush=True)


def _scenario_name(config: dict[str, Any]) -> str | None:
    """Classify the two intended OOD scenarios by their semantic coordinates."""
    if config.get("topology") != "dual_interface":
        return None
    background = config.get("background")
    if not isinstance(background, dict):
        return None
    obss = config.get("obss", background.get("obss"))
    if not isinstance(obss, dict):
        return None
    if obss.get("obss_profile", obss.get("profile")) != "mixed4x4":
        return None
    profile = background.get("background_profile", background.get("profile"))
    traffic = background.get("background_traffic", background.get("traffic"))
    if profile == "none" and traffic == "none":
        return "obss_only"
    if profile == "legacy_mixed8" and traffic == "udp_random_onoff":
        return "obss_plus_legacy_mixed8"
    return None


def _identity(config: dict[str, Any]) -> str:
    """Return the exact scenario configuration identity, excluding policy."""
    value = copy.deepcopy(config)
    for key in ("policy", "seed", "run", "run_id"):
        value.pop(key, None)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def scenario_templates(
    original_config: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Extract exact overlays and resolved identities from the original matrix."""
    document = load_yaml(original_config)
    templates: dict[str, dict[str, Any]] = {}
    identities: dict[str, str] = {}
    policies: dict[str, set[str]] = defaultdict(set)
    for policy in document.get("policies", []):
        if not isinstance(policy, dict) or policy.get("name") not in POLICIES:
            continue
        overlay = policy.get("config", {})
        if not isinstance(overlay, dict):
            raise ValueError("OOD policy config must be a mapping")
        probe = copy.deepcopy(document.get("base", {}))
        probe["topology"] = "dual_interface"
        probe["policy"] = policy["name"]
        for dotted, value in overlay.items():
            node = probe
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        scenario = _scenario_name(probe)
        if scenario is None:
            continue
        identity = _identity(probe)
        previous = identities.setdefault(scenario, identity)
        if previous != identity:
            raise ValueError(f"{scenario} policies do not preserve exact configuration")
        previous_overlay = templates.setdefault(scenario, copy.deepcopy(overlay))
        if previous_overlay != overlay:
            raise ValueError(f"{scenario} policies use different overlays")
        policies[scenario].add(policy["name"])
    if set(templates) != SCENARIOS:
        raise ValueError(f"expected exact OOD scenarios {sorted(SCENARIOS)}")
    for scenario in SCENARIOS:
        if policies[scenario] != POLICIES:
            raise ValueError(f"{scenario} lacks both fixed-link policies")
    return templates, identities


def semantic_scenario(
    config: dict[str, Any], identities: dict[str, str]
) -> str | None:
    """Return an exact input scenario or its validated resolved-output form."""
    scenario = _scenario_name(config)
    if scenario is None:
        return None
    # Input matrices retain the top-level ``obss`` block and can be compared
    # exactly. Simulator output uses a richer, renamed schema under
    # ``background.obss``; validate_run checks that form before collection.
    if "obss" in config and identities.get(scenario) != _identity(config):
        return None
    return scenario


def repair_config_paths(config_dir: Path = CONFIG_DIR) -> list[Path]:
    """Return the original and every existing OOD repair matrix."""
    paths = [config_dir / ORIGINAL_CONFIG.name]
    paths.extend(
        path
        for path in config_dir.glob(f"{REPAIR_PREFIX}*.yaml")
        if path.is_file()
    )
    return sorted(set(paths), key=lambda path: path.name)


def repair_result_roots(results_dir: Path = ROOT / "results") -> list[Path]:
    """Return the original and all direct OOD repair result roots."""
    roots = [results_dir / ORIGINAL_RESULTS.name]
    if results_dir.is_dir():
        roots.extend(
            child
            for child in results_dir.iterdir()
            if child.is_dir() and child.name.startswith(REPAIR_PREFIX)
        )
    return sorted(set(roots), key=lambda path: path.name)


def configured_seeds(
    paths: Iterable[Path], identities: dict[str, str]
) -> dict[str, set[int]]:
    """Collect every seed assigned to an exact scenario."""
    used: dict[str, set[int]] = defaultdict(set)
    for path in paths:
        for spec in expand_config(load_yaml(path)):
            scenario = semantic_scenario(spec["config"], identities)
            if scenario is not None:
                used[scenario].add(spec["seed"])
    return used


def attempted_seeds(
    roots: Iterable[Path], identities: dict[str, str]
) -> dict[str, set[int]]:
    """Collect seeds from completed and direct in-progress run directories."""
    used: dict[str, set[int]] = defaultdict(set)
    for root in roots:
        if not root.is_dir():
            continue
        for run_dir in root.iterdir():
            if not run_dir.is_dir():
                continue
            try:
                config = json.loads(
                    (run_dir / "resolved_config.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, TypeError):
                continue
            scenario = semantic_scenario(config, identities)
            seed = config.get("seed")
            if scenario is not None and isinstance(seed, int) and seed > 0:
                used[scenario].add(seed)
    return used


def all_used_seeds(
    paths: Iterable[Path],
    roots: Iterable[Path],
    identities: dict[str, str],
) -> dict[str, set[int]]:
    """Combine configured and attempted seeds for fresh allocation."""
    used = configured_seeds(paths, identities)
    for scenario, seeds in attempted_seeds(roots, identities).items():
        used[scenario].update(seeds)
    return used


def completed_policy_seeds(
    roots: Iterable[Path],
    identities: dict[str, str],
    validator: Callable[[Path], dict[str, Any]] = validate_run,
) -> dict[str, dict[GroupKey, set[str]]]:
    """Collect valid policies by exact scenario, seed, run, and commit."""
    completed: dict[str, dict[GroupKey, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for root in roots:
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
            scenario = semantic_scenario(config, identities)
            policy = config.get("policy")
            seed = config.get("seed")
            run = config.get("run")
            commit = build.get("project_git_commit")
            if (
                scenario is not None
                and policy in POLICIES
                and isinstance(seed, int)
                and seed > 0
                and isinstance(run, int)
                and run > 0
                and isinstance(commit, str)
                and commit
            ):
                completed[scenario][(seed, run, commit)].add(policy)
    return completed


def matched_and_missing(
    completed: dict[str, dict[GroupKey, set[str]]], target_groups: int
) -> tuple[int, int]:
    """Count commit-consistent matched groups and total target deficit."""
    matched_total = 0
    missing_total = 0
    for scenario in SCENARIOS:
        matched = sum(
            policies == POLICIES for policies in completed.get(scenario, {}).values()
        )
        matched_total += min(matched, target_groups)
        missing_total += max(0, target_groups - matched)
    return matched_total, missing_total


def plan_fresh_seeds(
    completed: dict[str, dict[GroupKey, set[str]]],
    used: dict[str, set[int]],
    target_groups: int,
) -> tuple[dict[str, list[int]], int]:
    """Allocate enough unused positive seeds independently per scenario."""
    plan: dict[str, list[int]] = {}
    missing_total = 0
    for scenario in sorted(SCENARIOS):
        matched = sum(
            policies == POLICIES for policies in completed.get(scenario, {}).values()
        )
        missing = max(0, target_groups - matched)
        missing_total += missing
        if not missing:
            continue
        scenario_used = used.get(scenario, set())
        candidate = max(FIRST_FRESH_SEED, max(scenario_used, default=0) + 1)
        seeds: list[int] = []
        while len(seeds) < missing:
            if candidate not in scenario_used:
                seeds.append(candidate)
            candidate += 1
        plan[scenario] = seeds
    return plan, missing_total


def round_document(
    templates: dict[str, dict[str, Any]],
    plan: dict[str, list[int]],
    round_number: int,
) -> dict[str, Any]:
    """Build a round containing both policies for every planned seed."""
    policies = []
    for scenario in sorted(plan):
        for policy in sorted(POLICIES):
            policies.append(
                {
                    "name": policy,
                    "seeds": list(plan[scenario]),
                    "config": copy.deepcopy(templates[scenario]),
                }
            )
    return {
        "extends": ORIGINAL_CONFIG.name,
        "name": f"prediction-obss-repair-v1-round{round_number}",
        "output_root": f"results/{REPAIR_PREFIX}_round{round_number}",
        "workers": 10,
        "policies": policies,
    }


def write_round_config(
    path: Path,
    templates: dict[str, dict[str, Any]],
    identities: dict[str, str],
    plan: dict[str, list[int]],
    round_number: int,
) -> int:
    """Atomically write and verify an exact, duplicate-free repair matrix."""
    document = round_document(templates, plan, round_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        yaml.safe_dump(document, output, sort_keys=False)
        temporary = Path(output.name)
    os.replace(temporary, path)
    specs = expand_config(load_yaml(path))
    spec_ids = {
        (
            json.dumps(spec["config"], sort_keys=True, separators=(",", ":")),
            spec["seed"],
            spec["run"],
        )
        for spec in specs
    }
    expected = 2 * sum(len(seeds) for seeds in plan.values())
    if len(spec_ids) != len(specs):
        raise ValueError(f"{path}: duplicate expanded specs")
    if len(specs) != expected:
        raise ValueError(f"{path}: generated {len(specs)} runs, expected {expected}")
    observed = {semantic_scenario(spec["config"], identities) for spec in specs}
    if None in observed or observed != set(plan):
        raise ValueError(f"{path}: generated specs changed OOD scenario identity")
    return len(specs)


def round_details(
    config_path: Path, results_dir: Path
) -> tuple[int, Path, int]:
    """Return round number, result root, and expanded run count."""
    match = ROUND_PATTERN.fullmatch(config_path.name)
    if match is None:
        raise ValueError(f"invalid OOD repair round config: {config_path}")
    document = load_yaml(config_path)
    output_root = Path(document["output_root"])
    if not output_root.is_absolute():
        output_root = results_dir / output_root.name
    return int(match.group(1)), output_root, len(expand_config(document))


def pending_round(
    paths: Iterable[Path], results_dir: Path
) -> tuple[int, Path, Path, int] | None:
    """Find the earliest generated round without a completion marker."""
    pending = []
    for path in paths:
        if ROUND_PATTERN.fullmatch(path.name) is None:
            continue
        number, root, count = round_details(path, results_dir)
        if not (root / ROUND_MARKER).is_file():
            pending.append((number, path, root, count))
    return min(pending, default=None, key=lambda item: item[0])


def next_round_number(paths: Iterable[Path]) -> int:
    """Allocate a distinct round number after all existing matrices."""
    numbers = [
        int(match.group(1))
        for path in paths
        if (match := ROUND_PATTERN.fullmatch(path.name)) is not None
    ]
    return max(numbers, default=1) + 1


def cleanup_repair_roots(roots: Iterable[Path]) -> int:
    """Remove only direct hidden .attempt-* directories in repair roots."""
    removed = 0
    for root in roots:
        if not root.name.startswith(REPAIR_PREFIX) or not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda path: path.name):
            if (
                child.is_dir()
                and not child.is_symlink()
                and child.name.startswith(".")
                and ".attempt-" in child.name
            ):
                shutil.rmtree(child)
                removed += 1
    return removed


def write_marker(path: Path, value: dict[str, Any]) -> None:
    """Atomically record that a launched round returned."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        json.dump(value, output, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def completed_rounds_and_streak(
    paths: Iterable[Path], results_dir: Path
) -> tuple[int, int]:
    """Count marked rounds and their trailing no-progress streak."""
    records = []
    for path in paths:
        if ROUND_PATTERN.fullmatch(path.name) is None:
            continue
        number, root, _ = round_details(path, results_dir)
        try:
            record = json.loads((root / ROUND_MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        records.append((number, record))
    streak = 0
    for _, record in sorted(records, reverse=True):
        if record.get("matched_after", 0) > record.get("matched_before", 0):
            break
        streak += 1
    return len(records), streak


def default_runner(config_path: Path) -> int:
    """Launch one matrix without rebuilding or running analysis."""
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/run_experiments.py"),
            str(config_path),
            "--no-build",
            "--no-analysis",
            "--resume",
        ],
        cwd=ROOT,
        check=False,
    ).returncode


def run_repair_loop(
    *,
    config_dir: Path = CONFIG_DIR,
    results_dir: Path = ROOT / "results",
    target_groups: int = TARGET_GROUPS,
    max_rounds: int = MAX_ROUNDS,
    max_no_progress_rounds: int = MAX_NO_PROGRESS_ROUNDS,
    runner: Callable[[Path], int] = default_runner,
    validator: Callable[[Path], dict[str, Any]] | None = None,
) -> int:
    """Run OOD repair rounds and return zero only when both targets are met."""
    templates, identities = scenario_templates(config_dir / ORIGINAL_CONFIG.name)
    paths = repair_config_paths(config_dir)
    completed_rounds, no_progress = completed_rounds_and_streak(paths, results_dir)
    collector_args: dict[str, Any] = {}
    if validator is not None:
        collector_args["validator"] = validator
    while True:
        paths = repair_config_paths(config_dir)
        roots = repair_result_roots(results_dir)
        completed = completed_policy_seeds(roots, identities, **collector_args)
        matched, missing = matched_and_missing(completed, target_groups)
        emit("PROGRESS", round=completed_rounds, matched=matched, missing=missing)
        if missing == 0:
            emit("FINAL", status="success", matched=matched, missing=0,
                 rounds=completed_rounds)
            return 0
        if no_progress >= max_no_progress_rounds:
            emit("FINAL", status="no_progress", matched=matched, missing=missing,
                 rounds=completed_rounds)
            return 3
        if completed_rounds >= max_rounds:
            emit("FINAL", status="exhausted", matched=matched, missing=missing,
                 rounds=completed_rounds)
            return 2

        pending = pending_round(paths, results_dir)
        if pending is None:
            number = next_round_number(paths)
            used = all_used_seeds(paths, roots, identities)
            plan, planned_missing = plan_fresh_seeds(
                completed, used, target_groups
            )
            if planned_missing != missing or not plan:
                raise RuntimeError("OOD repair plan does not match observed deficit")
            config_path = config_dir / f"{REPAIR_PREFIX}_round{number}.yaml"
            result_root = results_dir / f"{REPAIR_PREFIX}_round{number}"
            run_count = write_round_config(
                config_path, templates, identities, plan, number
            )
        else:
            number, config_path, result_root, run_count = pending

        cleanup_count = cleanup_repair_roots(roots + [result_root])
        emit("ROUND_START", round=number, matched=matched, missing=missing)
        emit("ROUND_CLEANUP", round=number, count=cleanup_count)
        emit("ROUND_GENERATED", round=number, runs=run_count)
        return_code = runner(config_path)
        completed_rounds += 1

        refreshed = completed_policy_seeds(
            repair_result_roots(results_dir), identities, **collector_args
        )
        new_matched, new_missing = matched_and_missing(refreshed, target_groups)
        write_marker(
            result_root / ROUND_MARKER,
            {
                "round": number,
                "runner_return_code": return_code,
                "matched_before": matched,
                "matched_after": new_matched,
                "missing_after": new_missing,
            },
        )
        emit("ROUND_FINISH", round=number, exit_code=return_code,
             matched=new_matched, missing=new_missing)
        no_progress = 0 if new_matched > matched else no_progress + 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-groups", type=int, default=TARGET_GROUPS)
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    parser.add_argument(
        "--max-no-progress-rounds", type=int, default=MAX_NO_PROGRESS_ROUNDS
    )
    args = parser.parse_args()
    for name in ("target_groups", "max_rounds", "max_no_progress_rounds"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    raise SystemExit(
        run_repair_loop(
            target_groups=args.target_groups,
            max_rounds=args.max_rounds,
            max_no_progress_rounds=args.max_no_progress_rounds,
        )
    )


if __name__ == "__main__":
    main()
