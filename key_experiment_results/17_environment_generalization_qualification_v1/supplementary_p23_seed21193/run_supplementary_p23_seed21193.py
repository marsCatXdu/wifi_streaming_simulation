#!/usr/bin/env python3
"""Run one clearly supplementary p23 paired trio with corrected validation."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


SIMULATION_COMMIT = "47e19962420bb7623784bc91b0c0d40fbf462b35"
NS3_COMMIT = "d2add90b452d600cfb4859baed8e9ea633519447"
EXPECTED_POLICIES = {
    ("mlo_str", "fixed_link_0"),
    ("dual_interface", "paired_value_duplication_t2"),
    ("dual_interface", "distributional_shadow_duplication_t2"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-driver", required=True, type=Path)
    parser.add_argument("--frozen-checkout", required=True, type=Path)
    parser.add_argument("--validator-checkout", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    recovery_spec = importlib.util.spec_from_file_location(
        "qualification_recovery_driver", args.recovery_driver.resolve()
    )
    if recovery_spec is None or recovery_spec.loader is None:
        raise RuntimeError("cannot load exact-run recovery support")
    recovery = importlib.util.module_from_spec(recovery_spec)
    recovery_spec.loader.exec_module(recovery)
    runner, _validator = recovery.load_frozen_runner(
        args.frozen_checkout.resolve(), args.validator_checkout.resolve()
    )
    if runner.project_commit() != SIMULATION_COMMIT:
        raise RuntimeError("frozen simulator checkout identity differs")
    if runner.NS3_UPSTREAM_COMMIT != NS3_COMMIT:
        raise RuntimeError("frozen ns-3 identity differs")

    config_path = args.config.resolve()
    document = runner.load_yaml(config_path)
    runtime = runner.validate_runtime_contract(document)
    specs = runner.expand_config(document)
    if (
        len(specs) != 3
        or {spec["seed"] for spec in specs} != {21193}
        or {spec["run"] for spec in specs} != {1}
        or {spec["scenario"]["scenario_id"] for spec in specs}
        != {"compound-shift-qualification-p23"}
        or {
            (spec["config"]["topology"], spec["config"]["policy"])
            for spec in specs
        }
        != EXPECTED_POLICIES
    ):
        raise RuntimeError("supplementary p23 matrix differs from the intended trio")

    run_ids = []
    for spec in specs:
        run_ids.append(
            {
                "run_id": runner.derive_run_id(
                    spec["config"],
                    spec["seed"],
                    spec["run"],
                    NS3_COMMIT,
                    SIMULATION_COMMIT,
                    runtime,
                    spec["scenario"],
                ),
                "topology": spec["config"]["topology"],
                "policy": spec["config"]["policy"],
                "seed": spec["seed"],
                "scenario_id": spec["scenario"]["scenario_id"],
            }
        )
    print(
        json.dumps(
            {
                "role": "supplementary_not_parent_qualification",
                "excluded_from_parent_population": True,
                "simulation_commit": SIMULATION_COMMIT,
                "runs": sorted(run_ids, key=lambda row: row["run_id"]),
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    if args.preflight_only:
        return

    sys.argv = [
        str(args.frozen_checkout.resolve() / "tools/run_experiments.py"),
        str(config_path),
        "--workers",
        "3",
        "--no-build",
        "--no-analysis",
    ]
    runner.main()


if __name__ == "__main__":
    main()
