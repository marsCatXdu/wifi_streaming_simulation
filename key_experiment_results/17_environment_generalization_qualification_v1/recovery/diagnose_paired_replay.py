#!/usr/bin/env python3
"""Report exact canonical-model replay differences for one failed run."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--frame-id", type=int, default=685)
    args = parser.parse_args()

    sys.path.insert(0, str(args.checkout.resolve() / "tools"))
    import numpy as np
    import validate_outputs as validator

    run_dir = args.run_dir.resolve()
    with (run_dir / "resolved_config.json").open(encoding="utf-8") as source:
        run_id = json.load(source)["run_id"]
    with (run_dir / "frames.csv").open(encoding="utf-8", newline="") as source:
        frames = list(csv.DictReader(source))
    with (run_dir / "paired_value_t2_decisions.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        decisions = list(csv.DictReader(source))

    frames_by_id = {int(row["frame_id"]): row for row in frames}
    primary_samples, primary_polling = validator._paired_value_t2_telemetry(
        run_dir, run_id, frames_by_id
    )
    evaluated = [row for row in decisions if row["feature_evaluated"] == "1"]
    frame_ids = [int(row["frame_id"]) for row in evaluated]
    matrix = np.vstack(
        [
            validator._paired_value_t2_feature_vector(
                frame_id,
                primary_samples[frame_id],
                primary_polling[frame_id],
                {
                    lag: primary_polling[frame_id - lag]
                    for lag in (1, 3, 8)
                },
            )
            for frame_id in frame_ids
        ]
    )
    context = validator._paired_value_t2_model_replay_context()
    primary_logits = context["primary_head"].decision_function(matrix)
    primary_probabilities = context["primary_head"].predict_proba(matrix)[:, 1]
    treated_logits = context["treated_head"].decision_function(matrix)
    treated_probabilities = context["treated_head"].predict_proba(matrix)[:, 1]

    mismatches: list[dict[str, object]] = []
    target: dict[str, object] | None = None
    keys = (
        "primary_bad12_logit",
        "primary_bad12_probability",
        "treated_bad12_logit",
        "treated_bad12_probability",
        "predicted_log_airtime",
        "predicted_secondary_airtime_us",
        "nonnegative_bad12_value",
        "value_per_cost_score_float32",
    )
    for index, row in enumerate(evaluated):
        frame_id = frame_ids[index]
        predicted_log = validator._paired_value_t2_ordered_cost_log(matrix[index])
        adjusted_log = min(
            max(predicted_log + math.log(context["smearing_factor"]), 0.0),
            math.log1p(1_000_000.0),
        )
        predicted_cost = max(math.expm1(adjusted_log), 1.0)
        nonnegative_value = max(
            float(primary_probabilities[index])
            - float(treated_probabilities[index]),
            0.0,
        )
        expected = {
            "primary_bad12_logit": float(primary_logits[index]),
            "primary_bad12_probability": float(primary_probabilities[index]),
            "treated_bad12_logit": float(treated_logits[index]),
            "treated_bad12_probability": float(treated_probabilities[index]),
            "predicted_log_airtime": predicted_log,
            "predicted_secondary_airtime_us": predicted_cost,
            "nonnegative_bad12_value": nonnegative_value,
            "value_per_cost_score_float32": float(
                np.float32(nonnegative_value / predicted_cost)
            ),
        }
        observed = {key: float(row[key]) for key in keys}
        tolerances = {
            "primary_bad12_logit": 1e-15,
            "primary_bad12_probability": 2e-16,
            "treated_bad12_logit": 1e-15,
            "treated_bad12_probability": 2e-16,
            "predicted_log_airtime": 0.0,
            "predicted_secondary_airtime_us": max(3e-11, predicted_cost * 2e-14),
            "nonnegative_bad12_value": 2e-16,
            "value_per_cost_score_float32": 0.0,
        }
        fields = {}
        for key in keys:
            difference = observed[key] - expected[key]
            ulp = max(math.ulp(observed[key]), math.ulp(expected[key]))
            fields[key] = {
                "observed": observed[key],
                "expected": expected[key],
                "difference": difference,
                "difference_ulps": abs(difference) / ulp,
                "tolerance": tolerances[key],
                "passes": abs(difference) <= tolerances[key],
            }
        result = {
            "frame_id": frame_id,
            "failing_fields": [
                key for key, value in fields.items() if not value["passes"]
            ],
            "fields": fields,
        }
        if result["failing_fields"]:
            mismatches.append(result)
        if frame_id == args.frame_id:
            target = result

    print(
        json.dumps(
            {
                "run_id": run_id,
                "evaluated_frame_count": len(evaluated),
                "mismatch_count": len(mismatches),
                "mismatch_frame_ids": [row["frame_id"] for row in mismatches],
                "target": target,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
