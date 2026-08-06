# Supplementary p23 seed 21193

This is an explicitly supplementary sensitivity trio for
`compound-shift-qualification-p23`.  Seed `21193` is the first integer after
the four frozen replicate seeds.  It was launched while the exact recovery
was still running to provide an early result if a frozen repair remained
slow.  It is excluded from the preregistered 576-run population and from every
qualification estimate, interval, and gate.

| Arm | Misses / generated | Miss rate | Completed frames | Sender airtime |
| --- | ---: | ---: | ---: | ---: |
| STR MLO | 1416 / 1800 | 78.6667% | 384 | 4873.288 ms |
| Score-aware T2 V2 | 1632 / 1800 | 90.6667% | 168 | 3633.940 ms |
| Distributional-shadow T2 | 1766 / 1800 | 98.1111% | 34 | 3557.063 ms |

The direction agrees with all four exact frozen p23 pairs: both selective
policies miss more deadlines than STR in this severe compound environment.
The low sender airtime is not an efficiency victory; the runs collapse and
complete far fewer frames.  Completed-frame P99 is unsupported for the
distributional arm and is not used here.

`supplementary_result.json` is the machine-readable reduction.  The copied
YAML, runner, service unit, and three-run manifest retain the exact execution
identity.  The ignored local raw directories are under
`results/environment_generalization_qualification_supplementary_p23_seed21193`.
