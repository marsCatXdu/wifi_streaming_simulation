# Legacy frame-delayed prediction evidence

The artifacts in this directory used frame-aligned rows to synthesize the
nominal polling observations. Their effective observation age was approximately
one video-frame period, not genuine 1 ms polling.

These artifacts, including every `polling_5ms` result, are retained only for
traceability. They are not formal prediction, model-selection, or closed-loop
evidence. Formal evidence must come from the `genuine_polling_v1` pipeline,
which records frame-independent reports and enforces staleness in `[1 ms, 2 ms)`.
Its compact evidence snapshot is under `../04_genuine_polling_v1/`.
