---
name: report-best-bbs
description: Report which BBS candidate matched the original S-parameters best, from the JSON a comparison run produced. Use after compare-bbs-candidates, when the user asks which model to keep.
---

# Reporting the best BBS

Read `compare.json` and answer from it. Do not re-derive, re-rank by eye, or
soften what it says.

## The answer

```bash
python - <<'PY'
import json; d = json.load(open("compare_run/compare.json"))
print(d["best"], d["ranking"], d["any_accepted"])
PY
```

Lead with the winner: **which file, which renorm impedance, and its status and
headroom.** The renorm impedance is the actionable part — it tells the user what
to use next time, not just which file to keep.

Then give the full ranking with each candidate's `status` and `headroom`, so the
choice is visible rather than asserted. `headroom` below 1 passes; smaller is
better.

## When nothing passed

`any_accepted` is false and `best` is null. Say that plainly. Give the ranking
anyway — the closest candidate and its headroom are what decide the next move —
and offer the two real options:

- **the sweep missed the range** — if the best headroom sits at one end of the
  renorm ladder, the centre is probably off that end. Re-run `sparabbs.dcr` and
  extend the ladder in that direction.
- **the spec is tighter than the conversion can reach** — if headroom is flat
  across the ladder, no renorm impedance is going to fix it; the model order or
  the criteria have to change.

Do not present a `FAIL` as a winner because it was the least bad. "Best of a
failing set" is a different statement from "best", and the user is deciding
whether to ship a model on it.

## Caveats worth carrying

- **Candidates that errored** did not produce a model to judge. List them
  separately with their `error`; a model that failed to simulate is not a model
  that failed to match.
- **Terms below the noise floor** were passed without being judged — decoupled
  port pairs at round-off. The comparison marks them, and they are in the JUnit
  output. Mention it if the user is relying on rail-to-rail coupling accuracy.
- **A narrow win is not a real difference.** If the top two headrooms are within
  a few percent of each other, say they are equivalent and let the user pick on
  another axis (model size, simulation speed) rather than presenting a
  meaningless ordering as a result.
