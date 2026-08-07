# Missing-run diagnostic before recovery

The one missing run is adaptive STR MLO run
`5663378425ecf42d9a21`, compound-shift p22, seed 21188, run 1.  The preserved
attempt directory is
`results/environment_generalization_adaptive_mcs_qualification_v1/shard1/.5663378425ecf42d9a21.attempt-17358`.

The executable aborted at simulated time 29.947258708 s on this ns-3 assertion:

```text
wifi-assoc-manager.cc:161
apInfo.m_linkId < m_scanParams.channelList.size()
```

The stack enters `WifiAssocManager::MatchScanParams` from beacon reception.
This is a native association/scanning invariant failure, before a complete
60-second result exists; it is not an output-validator rejection.  No source,
configuration, seed, or result was changed before preserving the attempt and
the 479-run partial snapshot.

Preserved attempt file identities:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `build_info.json` | 301 | `24bc40ee5e9981728c3d8442c65cd5e7f1b4f0381e3234e2a09fb985f47a502f` |
| `frames.csv` | 10011 | `b93e336d3e8a30ff5f8c4046739da9a0342a8c105669cb6c8480c061a8b7c952` |
| `policy_decisions.csv` | 6381 | `5a4df56688272d32f16f1b2bb7bd0d2be29aa2e42781b4a54adc158d1f9ef839` |
| `resolved_config.json` | 7110 | `142e0338424614680e60c9bac2f1bc15b75e78364ed483de734c25956e0e4c91` |
| `stdout.log` | 7796 | `2947919dba5005d2bcb9d6ba65964ac8d77abd807052737488539d4576d749c4` |

Recovery is limited to one retry of the exact same run using the same clean
simulation commit and executable.  A repeated deterministic assertion is not
grounds for silently changing the seed or mixing a patched binary into the
campaign.
