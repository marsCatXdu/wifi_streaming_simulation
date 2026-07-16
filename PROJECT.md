# Wi-Fi Streaming Simulation

This repository vendors ns-3.48 and adds the `wifi-streaming` contrib module
for frame-level latency experiments over single-link, dual-interface, and
native multi-link Wi-Fi.

## Baseline

The imported simulator source is the unmodified `ns-3.48` tag from
`https://gitlab.com/nsnam/ns-3-dev.git`.

```
tag:       ns-3.48
commit:    d2add90b452d600cfb4859baed8e9ea633519447
imported:  2026-07-16
```

The project specification is in `ns3_simulation_specification.md`.  Project
changes are confined to `contrib/wifi-streaming`, `experiments`, `tools`, and
project documentation unless a simulator extension point proves insufficient.

## Build

```
./ns3 configure --enable-examples --enable-tests
./ns3 build
./test.py -s wifi-streaming
```

Generated build trees and experiment results are not source artifacts and
must not be committed.
