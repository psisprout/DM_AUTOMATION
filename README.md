# DM_AUTOMATION

## sparabbs - BBS conversion checker

Validates a broadband-SPICE (BBS) model against the S-parameters it was fitted
from, in the Z domain, and reports the result as XML.

```
a.snp  (reference)  ──┐
                      ├─▶ generate PrimeSim deck ─▶ run ─▶ a re-extracted .sNp
a_sp.sp (BBS model) ──┘                                          │
                                                                 ▼
                         reference-node choice ─▶ Z comparison ─▶ report.xml
```

### Running it

```bash
python -m sparabbs.gui          # the GUI
python -m sparabbs.cli --help   # the same engine, headless
```

Needs Python 3.9+, numpy, and one Qt binding (PyQt5, PyQt6, PySide2 or
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

**4 · Compare & report.** Both networks go to Z via
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

Passivity and reciprocity are checked and reported but, by default, only warn —
the Z terms decide pass/fail. Tick *Fail on passivity / reciprocity violations*
to gate on them too.

### Output

`report.xml` carries the whole comparison, with `report.xsl` written alongside
it so a browser renders it as a report. `--junit` / *Save JUnit XML* emits one
testcase per Z term, which drops straight into a CI job.

```xml
<bbs_validation status="FAIL" nports="3">
  <meta>...<reference_node mode="port" description="all ports referenced to VDD_PMIC"/></meta>
  <criteria mag_err_pct="5" err_db="0.5" .../>
  <summary status="FAIL" terms="6" passed="3" warned="0" failed="3" points="400"/>
  <checks><check name="BBS-result passivity" status="PASS" detail="..."/></checks>
  <terms>
    <term name="Z11" i="1" j="1" port_i="VDD_CORE" kind="self" status="FAIL">
      <band name="1MHz-100MHz" status="FAIL" norm_err_pct="6.2" max_err_db="0.55" .../>
      <resonance status="WARN" ref_f="1.24e8" dut_f="1.31e8" shift_pct="5.6" .../>
    </term>
  </terms>
</bbs_validation>
```

Browsers block `file://` XSLT by default; serve the directory
(`python -m http.server`) or open the XML in the GUI's report pane.

### Batch use

```bash
python -m sparabbs.cli \
  --snp a.snp --bbs a_sp.sp --out run1 \
  --ground-pins GND \
  --cmd 'primesim_sub -spice -cpu {cpu} -i {deck}' --cpu 4 \
  --ref-mode port --ref-ports 3 \
  --xml report.xml --junit junit.xml
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
  report.py       XML and JUnit output
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
