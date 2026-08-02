## Specification: OBSS adaptive airtime duplication V1

Implement this as a new policy, `adaptive_airtime_duplication`. Preserve the existing `selective_duplication` controller, CLI parameters, CSV schema and experiment configuration.

“Run together” means both policies appear in the same paired OBSS experiment matrix, with separate ns-3 runs using identical seeds. They must never execute simultaneously in one simulation.

Based on repository commit `93ecd3d84443c9909c3246b38484610ef9873b68`. No files were changed.

### 1. Scope

The new policy retains:

- 5 GHz path 1 as primary.
- 2.4 GHz path 0 as delayed secondary.
- Frozen `commodity_polling_1ms_genuine_v1` predictor.
- T0/T1/T2/T4 decision opportunities.
- Whole-frame secondary copies.
- Same OBSS topology, traffic and propagation.

It changes only:

- The decision rule.
- Budget units from frame tokens to secondary sender PHY TX microseconds.
- Online threshold from fixed `0.20` to an adaptive shadow price.

Do not modify the combined-contention configuration or run it.

------

## 2. Airtime definition

For V1, define redundancy airtime as:

> Total PHY transmission duration of target-STA PPDUs on path 0 containing tagged redundant application packets (`path_id=0`, `copy_id=1`).

For every matching `PhyTxPsduBegin` event:
$$
a_{\mathrm{ppdu}}
=
\mathrm{WifiPhy::CalculateTxDuration}
(\mathrm{psduMap},\mathrm{txVector},\mathrm{phyBand})
$$
This measure:

- Includes retransmitted data PPDUs.
- Counts an aggregated PPDU once.
- Excludes contention waiting and backoff.
- Excludes ACK/BlockAck airtime transmitted by the AP.
- Excludes unrelated OBSS airtime.

Call it `secondary_sender_phy_tx_airtime`, not total channel airtime.

If one PPDU contains packets from multiple duplicated frames, allocate its duration among frames proportional to tagged MPDU bytes. The allocations must sum to the PPDU duration.

A PPDU containing both tagged redundant data and unrelated target-STA data should be logged as `mixed_ppdu`. The OBSS experiment should fail validation if this occurs, because path 0 should carry only the secondary streaming copies.

------

## 3. Optimization rule

The controller approximately solves:
$$
\max_{a_i\in\{0,1\}}\sum_i p_i a_i
\quad
\text{subject to}
\quad
\sum_i c_i a_i\le B
$$
where:

- $p_i$: current calibrated 5 GHz miss probability.
- $c_i$: estimated secondary-copy PHY TX airtime.
- $a_i$: launch decision.
- $B$: secondary-airtime budget.

Use the Lagrangian decision:
$$
u_i=p_i-\lambda_t\frac{\hat c_i}{c_{\mathrm{ref}}}
$$
Launch when:
$$
u_i>0
$$
and sufficient airtime tokens are available.

Here $c_{\mathrm{ref}}$ is the estimated airtime of a normal 12 KB P-frame. It normalizes costs so the initial shadow price remains interpretable.

Set the initial shadow price to `0.20`. Therefore, initially, a normal P-frame approximately requires $p_i>0.20$, matching the old policy. The threshold subsequently adapts.

A keyframe costing approximately four times as much initially requires much stronger evidence. That is correct when all frames have equal objective value. Frame importance weighting is outside V1.

------

## 4. Pre-launch airtime estimator

The actual airtime is unknown before transmitting, so admission requires a causal estimate.

For frame $i$:
$$
\hat c_i
=
s\,k_t
\left[
h+
\frac{8(B_i+38N_i)}{R}
\right]
$$
where:

- $B_i$: sum of `expectedMacServiceBytes` from the delayed secondary packetization plan.
- $N_i$: packet count.
- `38` bytes: QoS MAC/FCS/A-MPDU delimiter and padding allowance per MPDU.
- $R$: EHT MCS 5, 20 MHz, NSS 1, 800 ns GI data rate obtained from ns-3, not hard-coded.
- $h$: one EHT-SU PPDU preamble allowance. Prefer obtaining this through ns-3 PHY duration calculation.
- $s=1.25$: conservative safety factor.
- $k_t$: retry/aggregation inflation estimate, initially 1.

Expose a read-only descriptor from `MultipathSender`:

```
struct DelayedCopyDescriptor
{
    uint64_t frameId;
    uint32_t packetCount;
    uint64_t expectedMacServiceBytes;
    uint64_t deadlineTimeNs;
};
```

Add:

```
std::optional<DelayedCopyDescriptor>
GetDelayedSecondaryCopyDescriptor(uint64_t frameId) const;
```

After a duplicated frame reaches terminal MAC state, update:
$$
r_i=\max\left(1,\frac{c_i^{actual}}{c_i^{nominal}}\right)
$$
Do not use receiver completion or deadline outcome in this estimator.

------

## 5. Airtime token bucket

Configure:

```
airtime_budget_fraction = 0.02
bucket_horizon_us       = 1,000,000
```

`0.02` gives 20,000 µs of secondary sender TX time per simulated second. It is an initial approximation to the previous 30% nominal full-copy budget for this stream; the actual comparison must use measured airtime.

Bucket capacity:
$$
C=\rho H
$$
where $\rho=0.02$ and $H=1\,\mathrm{s}$, giving $C=20{,}000\,\mu s$.

Refill causally whenever the controller or meter receives an event:
$$
B_t=\min(C,\;B_{last}+\rho(t-t_{last}))
$$
Initialize the bucket full. Consequently, the finite-run allowance is:
$$
\rho T+C
$$
Record the initial capacity separately so it is not mistaken for steady-state budget.

### Reservation accounting

Maintain:

- `bucket_balance_us`: earned tokens minus measured airtime.
- `reserved_airtime_us`: estimated cost of launched but unsettled copies.
- `available_airtime_us = bucket_balance_us - reserved_airtime_us`.

Before launch, require:
$$
available\_airtime\_us+\epsilon\ge\hat c_i
$$
On launch, add $\hat c_i$ to the frame reservation. Do not immediately subtract it from `bucket_balance_us`.

When a tagged PPDU transmits:

1. Subtract its measured duration from `bucket_balance_us`.
2. Reduce the corresponding frame reservations by their allocated duration.
3. If actual airtime exceeds the reservation, allow the balance to become negative.
4. While available airtime is negative, prohibit new actions until refill clears the debt.

Release unused reservation when every secondary packet is ACKed or terminally dropped. Add a fallback settlement timer at:

```
frame deadline + MAC queue maximum delay + 1 ms
```

Log fallback settlement; normally it should never be required.

------

## 6. Adaptive shadow price

At every frame’s T0, after refilling the bucket, update:
$$
\lambda_{i+1}
=
\operatorname{clip}
\left[
\lambda_i+
\eta
\frac{a_i-\rho\Delta t_i}{c_{\mathrm{ref}}},
0,
1
\right]
$$
where:

- $a_i$: measured tagged secondary airtime since the previous T0.
- $\rho\Delta t_i$: airtime allowance earned during that interval.
- $\eta=0.01$: initial dual-update step.

Interpretation:

- Spending above the target increases $\lambda$, making actions harder.
- Spending below the target decreases $\lambda$, allowing lower-risk actions.
- The token bucket remains the admission constraint; the price controls which candidates consume it.

Freeze `0.02`, `0.01`, `1.25` and the price bounds before production runs. Do not tune them using the final seeds.

------

## 7. Per-stage decision state machine

For each eligible snapshot:

```
p = frozen_predictor.Score(sample)
cost = EstimateSecondaryAirtime(frame)
utility = p - shadow_price * cost / reference_cost
available = bucket_balance - total_reservations
```

Decision order:

1. Wrong path/copy/stage: ignore.
2. Frame already launched: `already_resolved`.
3. Not actionable: `not_actionable`.
4. `utility <= 0`: `price_rejected`.
5. Insufficient available airtime: `airtime_deferred`.
6. Otherwise reserve cost and request the secondary copy:
   - Success: `action`.
   - Failure: release reservation and record `launch_rejected`.

Unlike the existing controller, `airtime_deferred` must not resolve the frame. It may act at T1/T2/T4 after refill or a higher predicted probability. Only a successful launch permanently resolves it.

Retain the one-secondary-copy-per-frame invariant.

Extend `MultipathSender::RequestSecondaryCopy` without changing its current caller:

```
bool RequestSecondaryCopy(uint64_t frameId); // existing behavior

bool RequestSecondaryCopy(uint64_t frameId,
                          const std::string& reason);
```

The adaptive controller supplies a distinct reason. The existing overload continues writing `"calibrated risk threshold crossed"`.

------

## 8. New classes and policy identity

Add:

```
contrib/wifi-streaming/model/
    adaptive-airtime-duplication-controller.{h,cc}
    secondary-airtime-meter.{h,cc}
```

Add `AdaptiveAirtimeDuplicationPolicy` to `redundancy-policy.{h,cc}`. Its initial decision is identical to `SelectiveDuplicationPolicy`, but:

```
GetName() == "adaptive_airtime_duplication"
```

Do not parameterize or rename `SelectiveDuplicationPolicy`.

Register the new sources in `contrib/wifi-streaming/CMakeLists.txt`.

The passive `SecondaryAirtimeMeter` should be usable by:

- Existing fixed-threshold selective duplication.
- New adaptive duplication.
- Full duplication.

Only the adaptive policy supplies its callback to the controller. Metering the old policy must not influence its decisions or RNG state.

------

## 9. Configuration interface

Add independent CLI/configuration fields:

```
adaptiveAirtimeBudgetFraction
adaptiveAirtimeBucketHorizonUs
adaptiveAirtimeInitialShadowPrice
adaptiveAirtimeDualStep
adaptiveAirtimeCostSafetyFactor
adaptiveAirtimeCostEwmaAlpha
adaptiveAirtimeDecisionOffsetsUs
secondaryAirtimeMeterEnabled
```

Recommended defaults:

```
adaptive_airtime_budget_fraction: 0.02
adaptive_airtime_bucket_horizon_us: 1000000
adaptive_airtime_initial_shadow_price: 0.20
adaptive_airtime_dual_step: 0.01
adaptive_airtime_cost_safety_factor: 1.25
adaptive_airtime_cost_ewma_alpha: 0.10
adaptive_airtime_decision_offsets_us: [0, 1000, 2000, 4000]
secondary_airtime_meter_enabled: true
```

Add an `adaptiveAirtimeDuplication` object to `resolved_config.json`, including predictor provenance and the exact budget definition.

------

## 10. OBSS experiment matrix

Do not modify the existing closed_loop_selective_duplication_obss.yaml.

Create:

```
experiments/configs/closed_loop_adaptive_airtime_obss.yaml
```

It may extend the existing OBSS YAML but must replace the policy list with:

1. `fixed_link_1`
2. `selective_duplication`, unchanged threshold `0.20` and frame budget `0.30`
3. `adaptive_airtime_duplication`
4. `full_duplication`, primary path 1
5. MLO `NMaxInflights=1`

Enable the passive airtime meter for policies 2–4.

Use one output root:

```
results/adaptive_airtime_obss_v1/runs
```

Use identical paired seeds for every treatment. I recommend 30 production seeds; 10 is adequate only for an initial pilot.

Create a standalone runner:

```
tools/run_adaptive_airtime_obss.py
```

It should only:

1. Build `streaming-experiment`.
2. Run the new OBSS matrix with `--resume`.
3. Validate runs.
4. Aggregate and plot results.

Do not add a combined phase and do not modify the existing run_genuine_polling_pipeline.py.

------

## 11. Required output

### `adaptive_airtime_decisions.csv`

Include at least:

```
run_id, frame_id, sample_stage, sample_offset_us, sample_time_ns,
actionable, calibrated_probability,
estimated_airtime_us, reference_airtime_us,
shadow_price, normalized_cost, net_utility,
airtime_budget_fraction, bucket_capacity_us,
bucket_balance_us, reserved_airtime_us, available_airtime_us,
measured_airtime_total_us, decision, secondary_launched
```

### `secondary_airtime_events.csv`

One row per tagged PPDU:

```
run_id, time_ns, path_id, ppdu_duration_us,
tagged_mpdu_bytes, frame_ids, mixed_ppdu,
cumulative_tagged_airtime_us
```

### `secondary_airtime_summary.json`

Include:

```
tagged_ppdu_count
mixed_ppdu_count
tagged_secondary_tx_airtime_us
measurement_duration_us
tagged_secondary_tx_airtime_fraction
maximum_budget_debt_us
estimated_action_airtime_us
actual_to_estimated_airtime_ratio
forced_reservation_settlements
```

Keep `selective_duplication_decisions.csv` unchanged for the old policy.

------

## 12. Analysis

Add `tools/plot_adaptive_airtime_duplication.py` producing:

- Deadline miss ratio with 95% run-level CI.
- Paired adaptive-minus-fixed and adaptive-minus-MLO deltas.
- Maximum and P95 miss-burst lengths.
- Actual secondary sender airtime fraction.
- Reliability-versus-measured-airtime plot.
- Shadow-price and bucket-balance timeline.
- Action stage distribution.
- Estimated-versus-measured airtime calibration.

Validate the meter against existing logs:
$$
A_{\text{tagged}}
\le A_{\text{link0 PHY TX}}
$$
Also calculate paired incremental link-0 airtime:
$$
\frac{
A_{\text{policy, link0}}-
A_{\text{single5, link0}}
}{T}
$$
This should be close to the tagged meter result. A large difference indicates unattributed target-STA transmissions or a metering error.

------

## 13. Tests and acceptance criteria

Required unit tests:

- Shadow price rises after overspending and falls after underspending.
- Larger frames receive larger estimated cost.
- `price_rejected` may later become an action at T1/T2/T4.
- `airtime_deferred` does not resolve the frame.
- At most one secondary launch per frame.
- Measured PPDU airtime reduces both balance and reservation correctly.
- Actual cost above estimate creates debt and suppresses later actions.
- Retransmitted PPDUs are counted again.
- Multi-frame A-MPDU duration is counted once.
- Untagged PPDUs are ignored.
- Mixed tagged/untagged data is detected.

Required regression test:

Run the old fixed-threshold policy with the passive meter disabled and enabled. After removing run IDs, its frames, policy decisions and selective-controller decisions must be identical. This establishes that measurement did not alter behavior.

Required integration checks:

- Fixed and adaptive arms exist for every production seed.
- Adaptive action frame IDs exactly match duplicated frame IDs.
- Sum of airtime-event durations equals the summary total.
- No combined-contention run is generated.
- Every decision uses only telemetry available at its timestamp.
- Budget excess and maximum debt are explicitly reported.

A lower miss ratio from the adaptive arm will initially be confounded by higher actual airtime than the fixed-threshold arm. The first experiment establishes whether using the available budget helps. A later budget sweep or cost-matched rerun is necessary before attributing improvement to a better decision mechanism.