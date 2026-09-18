---
name: compare-bbs-candidates
description: Compare the BBS models an AEDT Network Data Explorer run produced against the original S-parameter file, in the Z domain, and write a JSON verdict. Use once the renorm sweep has finished and files like a_sp_0p01.sp exist next to a.s2p.
---

# Comparing BBS candidates against the original S-parameters

One command handles every candidate. It is command line only — there is a GUI,
but it is for a person reading plots, not for you.

## Step 1 — check the sweep actually finished

The conversion produces one file per renorm impedance, named after it:
`a.s2p` → `a_sp_0p1.sp`, `a_sp_0p01.sp`, `a_sp_0p001.sp`.

Before comparing, confirm you have one file per impedance in the list from
`dcr.json` (`renorm_labels`). If some are missing, say which and ask whether to
compare what exists or wait — do not quietly compare a partial sweep and then
call the winner "the best", because the best of three when five were asked for
is a different claim.

A file that exists but is empty or truncated is not finished either. The
comparison will fail on it and report the error rather than skipping it.

## Step 2 — run the comparison

```bash
python -m sparabbs.batch --snp a.s2p a_sp_*.sp --out compare_run
```

Each candidate gets a deck, a SPICE run, and a Z-domain comparison against the
reference. Useful flags:

- `--cmd` / `--cpu` — the simulator launcher, if it is not
  `primesim_sub -spice -cpu {cpu} -i {deck}`.
- `--wait` — minutes to wait per candidate. The launcher is normally a queue
  submit that returns immediately, so this is the real timeout, not the
  process. Raise it for a big model.
- `--ref-mode port --ref-ports N` — compare with every port referenced to port
  N, matching the reference node chosen earlier. Use this when the reference
  node is one of the ports rather than the model's own ground.
- `--z0` — required if the reference file states no reference impedance.
- `--require WARN` — accept a warning as good enough.

A candidate that is already a Touchstone file (`a_sp_0p01.s3p`) is compared
directly, with no simulation. That is how you re-rank against different criteria
without paying for the runs again.

## Step 3 — read the JSON, not the table

`compare_run/compare.json` is the output that matters:

- `candidates[]` — per model: `label`, `renorm_ohm`, `status`
  (`PASS`/`WARN`/`FAIL`), `headroom`, `margin`, `counts`, `error`.
- `ranking` — labels, best first.
- `best` — the winning candidate, or `null`.
- `any_accepted` — false when nothing met the criteria.

`headroom` is the worst metric as a fraction of its limit: below 1 passes,
above 1 fails, and smaller is better. It is what orders two models that both
pass, which `PASS`/`FAIL` cannot.

Exit code is 0 when something was accepted and 1 when nothing was.

## When a candidate errors

`error` is set and `status` stays `FAIL`. Read it rather than treating the model
as merely inaccurate — the usual causes are different in kind:

- **no `.sNp` appeared** — the simulation did not finish, or wrote elsewhere.
  The message lists what the run did write. This is not a bad model.
- **port count mismatch** — the netlist has a different number of pins than the
  reference has ports. The conversion produced something unexpected.
- **cannot read** — the file is truncated or was still being written.

Report these separately from the models that ran and simply did not match.

## What not to do

Do not re-run the comparison with looser criteria to manufacture a winner. If
nothing passes, that is the result; say so and hand back the headroom figures so
the user can decide whether to widen the renorm sweep or relax the spec.
