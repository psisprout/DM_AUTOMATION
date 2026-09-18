---
name: select-reference-node
description: Ask the user which port of an S-parameter file is the reference (ground) node, then extract DCR for the remaining ports and recommend renorm impedances for the BBS conversion. Use when starting a BBS conversion from a Touchstone file, or whenever a reference node must be chosen before impedances are read.
---

# Selecting the reference node and extracting DCR

Impedances only mean something relative to a reference node. Pick the wrong one
and every DCR, every renorm impedance and every comparison downstream is wrong
while looking completely ordinary. So the user chooses it, and you relay their
choice **exactly**.

## Step 1 — read the port names out of the file

Never type the port names from memory, from the filename, from a schematic, or
from what the user called them earlier. Get them from the file:

```bash
python -m sparabbs.dcr --snp <file>.sNp --list-ports
```

This prints JSON: `ports` is a list of `{index, name}`, and
`port_names_are_generic` is true when the file carries no real names and they
came out as `P1, P2, ...`.

## Step 2 — ask the user, quoting only those names

Show the list and ask which port is the reference node. Rules:

- **Offer only names the file gave you.** Do not add, translate, abbreviate,
  expand, correct the case of, or otherwise tidy a name.
- **If `port_names_are_generic` is true, say so.** `P1..Pn` carry no meaning, so
  tell the user the file has no port names and that they are choosing by index;
  they may need to check the schematic or the field-solver setup.
- Do not recommend a reference node, and do not guess from a name that looks
  like a ground (`GND`, `VSS`, `PGND`). Looking like ground is not being the
  reference node, and the file cannot tell you which one the user meant.

## Step 3 — accept only an exact answer

Pass the user's answer through verbatim:

```bash
python -m sparabbs.dcr --snp <file>.sNp --reference "<exactly what the user said>" \
    --json dcr.json
```

The script matches by exact name (ignoring only surrounding whitespace and
letter case) and **exits 3 with the list of real names** if there is no match.

When that happens, **do not guess**. Do not pick the nearest name, do not
fuzzy-match, do not assume a typo meant the obvious thing, do not fall back to
an index. Show the user the error, show the real names again, and ask once more.
The same applies when the user's instruction is ambiguous — "the ground one",
"the last one", two names that both fit — ask which exact name they mean.

A numeric answer is the one exception: an index is unambiguous, so
`--reference 3` is fine when the user gave a number. Echo back the name it
resolved to so they can catch a mistake.

## Step 4 — report what came back

`dcr.json` holds, for each non-reference port, `dcr_ohm` with the frequency it
was read at, `z_max_ohm` (the anti-resonance peak), and `settled`. It also holds
`renorm_center_ohm` and `renorm_impedances`, the ladder to try in the BBS
conversion, plus `renorm_labels` (`0p00776` and so on) for naming the runs.

Pass on any `warnings` rather than burying them. Two matter:

- **`settled: false`** — `Re(Z)` is still moving at the lowest frequency in the
  file, so that DCR is not a DC value. Say so; the number is a lower bound at
  best.
- **lowest frequency above 1 MHz** — the file simply does not reach low enough
  for DCR to mean anything. Say so plainly rather than quoting the number as if
  it were a DC resistance.

## What this skill does not do

It does not run the BBS conversion, and it does not choose the reference node.
If the user asks you to pick one, explain that the file cannot tell you and ask
them to choose.
