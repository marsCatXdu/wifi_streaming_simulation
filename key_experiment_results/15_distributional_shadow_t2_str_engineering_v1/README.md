# Distributional-shadow T2 closed-loop engineering v1

This directory archives the first closed-loop qualification of the compiled
distributional predictor, congestion-aware shadow price, and repayable
future-credit allocator.  It compares the selective full-copy policy with
same-build STR MLO on the 48 already-opened seeds `1251` through `1298`.
Reserved confirmation seeds `1301` through `1348` were not read.

## Result

The candidate passes every frozen STR performance and resource gate, but it
does not meet the promotion targets and does not replace score-aware V2.

| Metric | Distributional shadow T2 | STR MLO | Paired candidate-minus-STR 95% interval |
| --- | ---: | ---: | ---: |
| All-generated deadline misses | 455/86,400 (0.5266%) | 691/86,400 (0.7998%) | [-0.3935, -0.1597] percentage points |
| Mean per-run completed-frame P99 | 16.832 ms | 18.875 ms | [-3.057, -1.109] ms |
| Sender PHY airtime | 1.1662 ratio | 1.0000 | [1.1405, 1.1906] ratio |
| Background-throughput loss | 0.0050% | 0% reference | [0.0026%, 0.0074%] |

The result is a 34.15% relative miss reduction.  It passes the requirement
that the sender-airtime ratio's upper confidence endpoint be at most 1.20,
but it misses both the 0.50% engineering miss target and the longer-term 50%
relative-reduction target.  The completed-P99 target of at most 17 ms passes.

The paired result is heterogeneous.  Misses improve/tie/worsen on 32/8/8
runs, completed P99 improves/worsens on 35/13, and 26/48 pairs improve both.
Although the campaign-level airtime gate passes, 21/48 individual runs have
a sender-airtime ratio above 1.20.  Sender-airtime ratio correlates strongly
with the P99 delta (`r = 0.798`) and weakly with the miss delta (`r = 0.257`).

## Mechanism

The full-copy action remains highly effective once selected:

- The primary copy misses 1,232 deadlines.
- The policy acts on 809 of them, for 65.67% capture.
- It rescues 777 acted misses, for 96.04% conditional rescue.
- Only 32 acted primary misses remain late or incomplete.

The 455 final misses decompose into 283 shadow-price rejections, 72 startup
history frames, 48 restricted I-frames, 11 horizon-credit rejections, 32
acted but unresolved frames, and 9 frames outside the decision window.  The
policy launches 8,336 secondary copies: 4,163 accelerate an already-on-time
primary completion, 3,364 do not improve completion, 777 rescue misses, and
32 fail to rescue.  Every ledger repays by measurement stop; maximum
transient debt is 161.309 ms and minimum final balance is 1.037 ms.

## Exact comparison with V2

The same 48 seeds permit an unconfounded action-selection comparison: all
86,400 primary-copy deadline outcomes match between V2 and this candidate.

| Metric | Score-aware V2 | Distributional shadow T2 | Candidate-minus-V2 95% interval |
| --- | ---: | ---: | ---: |
| Final misses | 495 (0.5729%) | 455 (0.5266%) | [-0.1181, 0.0255] percentage points |
| Mean per-run completed P99 | 17.192 ms | 16.832 ms | [-0.687, -0.041] ms |
| Actions | 4,944 | 8,336 | - |
| Captured primary misses | 771 | 809 | - |

The candidate's miss interval versus V2 includes zero, while its P99 interval
is strictly negative.  It adds 3,392 net actions for only 38 additional
captured primary misses, or 89.3 added actions per extra capture.  V2-only
actions contain 108/1,142 primary misses (9.46%); candidate-only actions
contain 146/4,534 (3.22%).  Thus the new runtime spends much of its extra
airtime accelerating on-time frames rather than finding deadline rescues.
Selection efficiency, not the full-copy rescue action, remains the limiting
mechanism.  V2 stays the engineering champion.

## Evidence and provenance

- Simulation project commit:
  `e2c770b21cb8f69318a8cd5958815f4ab9c09392`.
- ns-3 upstream commit:
  `d2add90b452d600cfb4859baed8e9ea633519447`.
- Strict validator commit:
  `34e9296108f54ea0738cbc43b7b6c20df9d419dd`.
- Qualification analyzer commit: `bf9e5c1`.
- Exact V2 comparison and plotting commit: `544008c`.
- Runtime-contract SHA-256:
  `33b16c62848d0b724d347b791650e805c0fe2611eaf44ac4079b93cb59b5f4fa`.
- Portable-model SHA-256:
  `03e9e36f6dbec6457a25768571cb71a4dd860e737c406a92ddf2de00024a08a6`.
- Shadow-reference SHA-256:
  `f73ca45c059653448d1006f4250ec114538aaa645482a11140849025436b5502`.

All 96 candidate/STR runs were independently and strictly revalidated before
the report was generated.  The exact candidate archive is retained on the
experiment VM at
`/home/jingweili/distributional-shadow-t2-engineering-e2c770b-raw.tar.zst`
(89,685,591 bytes, SHA-256
`fe1fe1532655ca4d422612b1bbddb8e44869d94535286816941cbef0c3a6cb27`).
The complete same-build STR archive is
`/home/jingweili/str-same-commit-e2c770b-complete-raw.tar.zst`
(5,755,083 bytes, SHA-256
`9ff159b9ce2752da58834c7a3804bdcd52747b76f37c2c2f1bea5754a39038a1`).
Both pass `zstd -t`.  These host paths are not durable public storage; a
checksum-preserving release upload remains a reproducibility task.

The V2 comparison verifies V2's exact 94,939,663-byte raw archive (SHA-256
`382e4a3508cd013dc028b849301096054c12eef4cb302ed101f14d0434d6da3f`)
and its embedded schema-compatible 96-run strict report.  It freshly validates
all 48 distributional-policy runs with the current validator.

## Artifacts

- `distributional_shadow_t2_str_engineering.{json,md}`: authoritative paired
  qualification, promotion decision, source closure, and heterogeneity.
- `policy_diagnostics.json`: terminal-decision, action-outcome, congestion,
  chronology, and ledger decomposition.
- `paired_metrics.csv`: all 48 reconstructed matched-run metrics.
- `aggregate.json`: standard plotting aggregate reconstructed from raw data.
- `figures/`: paired performance, resource, policy, and allocator diagnostics.
- `figures/standard/`: completion CDF/PDF, deadline, burst, background, and
  redundancy figures in the repository's historical format.
- `v2_comparison/`: exact action-transition, paired metric, report, and figure
  comparison with score-aware V2.

## Reproduction

Restore the two checksum-bound raw archives, then run:

```bash
MPLCONFIGDIR=/tmp/wifi-streaming-mpl \
  .venv/bin/python \
  tools/analyze_distributional_shadow_t2_str_engineering.py \
  POLICY_RUN_ROOT STR_RUN_ROOT \
  --output-directory OUTPUT_ROOT \
  --policy-archive POLICY_ARCHIVE \
  --str-archive STR_ARCHIVE \
  --workers 12 \
  --require-str-victory

MPLCONFIGDIR=/tmp/wifi-streaming-mpl \
  .venv/bin/python \
  tools/plot_distributional_shadow_t2_str_engineering.py \
  OUTPUT_ROOT/distributional_shadow_t2_str_engineering.json \
  OUTPUT_ROOT/policy_diagnostics.json \
  --output-directory OUTPUT_ROOT/figures

MPLCONFIGDIR=/tmp/wifi-streaming-mpl \
  .venv/bin/python \
  tools/plot_results.py OUTPUT_ROOT/aggregate.json \
  --output-dir OUTPUT_ROOT/figures/standard
```

For the exact V2 comparison, use the archived V2 qualification report and
raw archive with `tools/compare_distributional_shadow_t2_v2.py --help`.
