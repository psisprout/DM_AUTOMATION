# DM_AUTOMATION

## sparabbs - BBS conversion checker

Validates a broadband-SPICE (BBS) model against the S-parameters it was fitted
from, in the Z domain.

```
a.snp  (reference)  ──┐
                      ├─▶ generate PrimeSim deck ─▶ run ─▶ a re-extracted .sNp
a_sp.sp (BBS model) ──┘                                          │
                                                                 ▼
                    reference-node choice ─▶ Z comparison ─▶ tree + plots
```

### Running it

```bash
python -m sparabbs.gui          # the GUI
python -m sparabbs.cli --help   # the same engine, headless
```

Needs Python 3.8+, numpy, and one Qt binding (PyQt5, PyQt6, PySide2 or
PySide6) for the GUI. `matplotlib` is optional and only used by *Plot selected
term*. The CLI needs no Qt at all.

### The four steps

**1 · Inputs.** Point at `a.snp` and `a_sp.sp`, press *Load and parse*. There is
nothing to fill in.

* Port count, frequency grid and port names come from the `.snp`.
* Z0 comes from the file (`# ... R 50`, or v2 `[Reference]`). If the file
  states none, the Z0 box unlocks and your value is used for both the deck and
  the comparison. Files without an `R` token are common, and Touchstone's
  default of 50 Ω is often wrong for a PDN — many are 1 Ω.
* The pin-to-port mapping is worked out for you and shown read-only: pin *k*
  drives port *k*, and any surplus trailing pins are tied to global `0`. That
  covers the usual BBS shapes — N pins for N ports, or N + 1 with a reference
  pin.

The one thing that would otherwise fail silently is a subcircuit whose pin
order differs from the Touchstone port order, which transposes the Z matrix
while leaving Z11…Znn looking healthy. So when the pin names and the port names
are the same set in a different order, the mapping follows the **names** rather
than the positions and says so above the table. When the names only partly
overlap it stays positional and warns.

Grounding a port, or picking which node the others are measured against, is not
done here — that is step 3, where it applies to both networks at once.

**2 · Deck & run.** *Generate deck* writes `bbs2spara.sp`:

```spice
.inc '/path/to/a_sp.sp'
P1 VDD_CORE 0 port=1 z0=50 dc=0 ac=0
...
XDUT VDD_CORE VDD_IO VDD_PMIC 0 pdn3_bbs
.ac poi 400 1000 1041.2 ...        $ exactly the reference grid
.lin sparcalc=1 format=touchstone filename='bbs_sparam'
```

`.AC POI` sweeps the reference file's own frequency points, so the comparison
needs no interpolation. A 0 Hz point cannot be swept by `.AC`; it is dropped
and noted in the deck and the report. *Max sweep points* decimates a very long
grid (endpoints are kept) if the run would otherwise be too slow.

The command template defaults to `primesim_sub -spice -cpu {cpu} -i {deck}`;
`{deck}`, `{deck_abs}`, `{cpu}` and `{dir}` are substituted.

**The launcher exiting does not mean the simulation ran.** A submit command
queues the job and returns immediately, so after it exits sparabbs keeps
watching the working directory, logging files as they appear, until a matching
`.sNp` shows up *and stops growing* — a large Touchstone file arriving over NFS
is visible long before it is complete. *Wait for result* caps that (0 waits
indefinitely) and *Stop waiting* ends it early; either way you get a list of
what the run did write. The reference `.snp` and any other file that was
already in the directory are never mistaken for the result.

A PrimeSim run also drops `ac0.ac` and `lin0.lin` next to the Touchstone
file; those are normal and appear before it. The `.LIN` options stay editable
in case a simulator version spells them differently — `{base}` is the output
basename, and the deck preview shows the line that gets written — but the
default is the spelling confirmed to work, quotes included.

**3 · Reference node.** How the raw N-port is referenced before Z is computed.
The same transform is applied to *both* networks, so the comparison stays
like-for-like.

| Mode | What it does |
|---|---|
| Raw ports | no transform; ports are already referenced to the model's ground |
| Reference every port to one port | `Z'ᵢⱼ = Zᵢⱼ − Zᵢᵣ − Zᵣⱼ + Zᵣᵣ`, dropping port *r* — the ADS "pick the ground node" behaviour |
| Short selected ports to ground | Schur complement of the shorted block |
| Leave selected ports open | delete those rows and columns |

Raw mode is the honest *"did the conversion work"* test, port-for-port. The
other modes answer *"does the difference matter once it is hooked up the way I
use it"*.

**4 · Compare.** Both networks go to Z via
`Z = (I−A)⁻¹(I+A)·diag(z0)` with `Aᵢⱼ = Sᵢⱼ·√(z0ᵢ/z0ⱼ)`, renormalizing the DUT
first if its Z0 differs. Every Zᵢⱼ is scored per frequency band:

* `norm_err_pct` — max `|Z_dut − Z_ref|` as a percentage of the band's peak
  `|Z_ref|`. Normalizing to the band peak is what keeps near-null off-diagonal
  terms from reporting meaningless percentages.
* `max_err_db`, `rmse_db`, `max_phase_deg` — computed only where
  `|Z_ref|` is at least 1 % of the band peak, for the same reason.
* Diagonal terms also get anti-resonance matching: peak frequency shift and
  peak `|Z|` error, which is the acceptance criterion that actually matters
  for a PDN.

A metric above its limit fails; above 60 % of it warns. A band takes its worst
metric, a term its worst band or peak, and the run its worst term. Passivity
and reciprocity are checked and reported but, by default, only warn — the Z
terms decide pass/fail. Tick *Fail on passivity / reciprocity violations* to
gate on them too.

**5 · Plot.** An N×N grid of checkboxes, one per Zᵢⱼ, tinted green/amber/red by
verdict so a 24-port matrix is scannable at a glance. Click a row or column
header to toggle that whole row or column; tick individual cells for anything
else. The quick buttons *add* to what is already ticked, so *Diagonal* then
*Off-diagonal* gives you both.

| Button | Picks |
|---|---|
| All / None / Invert | the obvious |
| Diagonal (Zii) | the self impedances |
| Upper triangle / Off-diagonal | the unique half of a reciprocal matrix |
| Failing / Failing + warning | exactly what did not match — the reason you ran this |
| Worst *n* | the *n* largest errors, worst first |
| Select by name | ports matching a glob like `VDD_PMIC*`; *both ends* narrows it to rail-to-rail |

*Fold Zji onto Zij* stops a reciprocal pair being drawn twice. Reference is
drawn solid and the BBS result dashed in the same colour, so a term that
matches reads as a single line. Choose magnitude, magnitude with an error
panel, error alone, or phase, overlaid in one axes or one subplot per term.
Plotting needs `matplotlib`; everything else works without it.

### Output

The window is the report: the tree on tab 4 carries every band and resonance
figure, and tab 5 plots them. For a CI gate, *Save JUnit XML* (or `--junit`)
writes one testcase per Z term, and the CLI exits non-zero on a failing
comparison.

### Batch use

```bash
python -m sparabbs.cli \
  --snp a.snp --bbs a_sp.sp --out run1 \
  --ground-pins GND \
  --cmd 'primesim_sub -spice -cpu {cpu} -i {deck}' --cpu 4 \
  --ref-mode port --ref-ports 3 \
  --junit junit.xml
```

Exit code is 1 when the comparison fails, so it gates a pipeline directly.
`--use-snp` skips the run and compares an existing `.sNp` — useful when the
simulation already ran, or for re-scoring against different criteria.

### Layout

```
sparabbs/
  touchstone.py   Touchstone v1/v2 read/write, S <-> Z, renormalization
  netlist.py      .subckt discovery and pin order
  deck.py         deck generation and pin-map validation
  runner.py       launching the simulator, finding the .sNp it wrote
  compare.py      reference-node transforms, banding, metrics, verdicts
  plotting.py     term selection idioms and the plots themselves
  report.py       JUnit output for CI
  cli.py          headless driver
  gui/
    main_window.ui  Qt Designer layout (XML)
    qtcompat.py     PyQt5 / PyQt6 / PySide2 / PySide6 shim
    app.py          window logic
tests/
  make_fixtures.py  synthetic 3-port PDN used by the tests
  test_engine.py    engine tests
  test_gui.py       GUI tests, driven on Qt's offscreen platform
```

### Tests

```bash
python tests/make_fixtures.py
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
```

The fixture is a three-node RLC mesh defined by a nodal admittance matrix, so
the reference-node transforms are checked against an independent MNA solve
rather than against the same formula they implement.

### Known limits

* `.AC` cannot evaluate 0 Hz, so a DC point in the reference file is excluded
  from the comparison rather than compared. DC resistance needs a separate
  `.OP` extraction.
* The netlist scanner reads the file it is given; it does not follow
  `.include` to find a `.subckt` defined elsewhere.
* Touchstone `H` and `G` parameter files are rejected; `S`, `Z` and `Y` are read.
* Verified on Python 3.8.20 (numpy 1.24.4, PyQt5 5.15.11, matplotlib 3.7.5)
  and on 3.11. `sh tests/run_on_py38.sh` builds a 3.8 environment and runs
  the whole suite in it; `tests/test_compat.py` additionally scans the
  sources for anything newer than the floor, because a builtin generic like
  `tuple[int, int]` parses on every version and only fails when evaluated.
