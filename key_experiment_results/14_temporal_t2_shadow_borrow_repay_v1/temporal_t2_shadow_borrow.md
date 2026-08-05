# Temporal-T2 shadow-priced borrow/repay screen

This opened-data mechanism screen changes only credit liquidity after the frozen opportunity-price test. Every borrowed reservation must be repaid by measurement stop.

## Policy outcomes

| Regime | Credit | Actions | Borrowed | Captured primary misses | Capture | DR miss | DR late18 | Mean reservation | Max debt |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| global | strict_current_credit | 15,392 | 0 | 989 | 48.10% | 1.479% | 2.447% | 318.06 ms | 0.00 ms |
| global | shadow_borrow_repay | 15,586 | 8,692 | 1,505 | 73.20% | 0.754% | 1.983% | 322.07 ms | 132.06 ms |
| congestion_tertile | strict_current_credit | 15,520 | 0 | 1,056 | 51.36% | 1.389% | 2.395% | 320.71 ms | 0.00 ms |
| congestion_tertile | shadow_borrow_repay | 15,719 | 8,441 | 1,517 | 73.78% | 0.748% | 2.002% | 324.82 ms | 144.82 ms |

## Observed primary misses by decision route

| Regime | Credit | Opportunity reject | Current-credit reject | Horizon reject | Strict action | Borrowed action | Gate |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| global | strict_current_credit | 146 | 813 | 0 | 989 | 0 | 108 |
| global | shadow_borrow_repay | 416 | 0 | 27 | 192 | 1,313 | 108 |
| congestion_tertile | strict_current_credit | 144 | 748 | 0 | 1,056 | 0 | 108 |
| congestion_tertile | shadow_borrow_repay | 407 | 0 | 24 | 218 | 1,299 | 108 |

## Frozen engineering screen

Overall: **PASS**.

| Check | Actual | Pass |
| --- | ---: | ---: |
| primary_miss_capture_fraction | 0.73784 | yes |
| dr_deadline_miss_probability | 0.0074809 | yes |
| dr_completed_late18_ratio | 0.0200181 | yes |
| canonical_reservation | 370963 | yes |
| repayment | 1036.76 | yes |

## Interpretation boundary

- This screen reuses the 96 opened randomized groups and is not independent confirmation.
- The action-clean lag-8 population excludes startup and action-dirty rows, so its absolute risks are not directly comparable with all-generated-frame closed-loop campaigns.
- Doubly robust frame replay does not reproduce action-induced contention, queues, sender airtime, background throughput, or completed-frame P99.
- Borrowing deterministic accounting credit can create transient airtime bursts; only a closed-loop campaign can qualify the less-than-1.20 sender-airtime and background-throughput gates.
- Reserved seeds 1301 through 1348 must not be read by this screen.
