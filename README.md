# DM_AUTOMATION

Batch eye measurement for Synopsys Custom WaveView (ACE Tcl), run headless with
`-no_gui`, producing both a CSV summary and a re-openable WaveView session per
FSDB.

## Run

Everything goes through the site wrapper `sx_sub`:

| | |
|---|---|
| `sx_sub -no_gui <script>` | headless — measurement only, draws nothing |
| `sx_sub` | opens the WaveView GUI; load a script via **Run ACE script** |

```sh
sx_sub -no_gui measure_eye.tcl cfg/lp5x_write.tcl     # LP5x write
sx_sub -no_gui measure_eye.tcl cfg/nand_read.tcl      # NAND read (template)
```

Run it from the directory holding the `.fsdb` files. If the wrapper does not
forward arguments, pass the config by environment instead:

```sh
DM_EYE_CFG=cfg/lp5x_write.tcl sx_sub -no_gui measure_eye.tcl
```

`measure_eye.tcl` does not trust argument position — it takes the first argv
entry that is a readable file other than itself, so an added `-no_gui` or a
repeated script name does no harm. The resolved config is echoed as
`[eye] config: ...` on startup.

Outputs land in `out/`:

| file | contents |
|---|---|
| `out/<cfg>_eye.csv` | `fsdb,dq0..dq7,dmi0,dq8..dq15,dmi1,vref0,vref1` |
| `out/<fsdb>_<cfg>.sx` | **WaveView session — every bit's eye on a grid, at the measured vref** |

The `.sx` is written directly as text during the headless measurement pass, so
one `-no_gui` run produces both the numbers and something to open. No ACE
display commands, no GUI scripting — `sx_display_eye` refuses to run under
`-no_gui` anyway.

With `session_scope all_fsdb` the per-fsdb files are replaced by a single
`out/<cfg>.sx` holding every fsdb as `wdf 0,1,2...`, each panel tagged with the
matching `fidx`.

Panel templates ship in `ref/` — nothing needs to be supplied. To bake in
colours or mask settings tuned in the GUI, save a session over
`ref/<config name>.sx`; see [ref/README.md](ref/README.md).

To look at a result: `sx_sub`, then open `out/corner_tt_1p0v_lp5x_write.sx`.

## Adding a protocol

A protocol is a file in `cfg/` plus its own reference `.sx` (copy an existing
one). No code changes.
`cfg/nand_read.tcl` is a filled-in skeleton to copy. What a config owns:

| | |
|---|---|
| `ui` `eye_shift` `vac` `vref_sweep` `eye_type` | timing and the measurement |
| `data_fmt` `strobe_fmt` | **signal naming**, as format strings |
| `byte_order` + `bytes` | how many bytes, which bits, which pad instance and strobe index — and the CSV column order |
| `session_template` `grid_cols` `session_scope` | `.sx` output |
| `session_fmt` | how each `.sx` field is written (see below) |
| `session_subst` | **which panel key holds which value** |
| `measure_args` | **the `sx_measure_eye` argument list** |

Signal names are format strings rather than code, so a different netlist
convention is a config edit:

```tcl
dict set CFG data_fmt   {v(%prefix%_%bit%)}
dict set CFG strobe_fmt {v(%prefix%_%pdqs%%idx%,%prefix%_%ndqs%%idx%)}
```

`%prefix%` is the byte's `pad_prefix`, `%bit%` the bit name, `%idx%` its strobe
index, `%pdqs%`/`%ndqs%` the strobe p/n roots.

### Masks other than rectangular

A hexagonal-mask measurement (CA, typically) does not take the same
`sx_measure_eye` arguments as the rectangular `ddr4` one, and its `.sx` panel
does not carry the same field names. Both are config data:

```tcl
dict set CFG measure_args {type=%type% vref=%vref% vac=%vac%}
dict set CFG session_subst [dict create em_vref vref  em_vac cfg:vac  ...]
```

`measure_args` is the literal argument list, over `%type% %vref% %vac% %ui%
%shift%`. `session_subst` maps a key **in that protocol's reference panel** to
where its value comes from — `vref`, `trig`, `sig`, `fidx`, `attr`, or
`cfg:<key>` for any config value. If the hexagonal panel calls it `hex_vref`,
rename the key; a key that is not in the panel is reported at startup rather
than silently dropped.

`cfg/lp5x_ca.tcl` is a skeleton for this, with the two spots to fill marked.
Diff that protocol's reference `.sx` against `ref/lp5x_write.sx` to see which
fields actually changed.

## How the session is built

A session is a text file, and the measurement pass already knows every signal
name and the per-byte vref, so `lib/session.tcl` just writes one.

The panel block is **not** hard coded. A real saved session is read from
`ref/waveview.session` and its `panel_begin` / `line` / `panel_end` lines are
reused verbatim, with only these substituted per bit:

| field | value |
|---|---|
| `pidx` / `ridx` / `cidx` | grid position, row-major over `grid_cols` |
| `eye_ext` | `<fidx>|<differential strobe>` for that byte |
| `em_vref` | the byte's measured vref |
| `eye_width` / `eye_shift` / `em_vac` | UI / phase / vac from the config |
| `fidx` and `name=` on the `line` | source file index and the bit's signal |
| `attr=` on the `line` | trace colour, `<i>:<i mod attr_colors>:...`, cycled per byte |
| `eye_meas` | the config's `eye_type` — the same value passed to `sx_measure_eye type=` |

Every other token is copied byte for byte, so mask settings, colours and
anything this code does not model survive untouched. If a key it means to
substitute is missing from the reference it says so instead of silently
emitting the reference value.

Panels fill `pidx` from 0 in CSV column order; the last row is padded with the
reference's empty panel to complete the grid.

### Units

The `.sx` format is not uniform — times are scientific notation, voltages keep
an SI suffix — while config values are SPICE style. `session_fmt` says how each
field is written:

```tcl
dict set CFG session_fmt [dict create eye_width sci eye_shift sci em_vac milli em_vref milli]
```

`sci` gives `-156.25p` -> `-1.5625e-10`, `milli` gives `0.1375` -> `137.5m`.
Anything unlisted is copied verbatim.

## How the vref is chosen

`ddr4_vref_sweep=` on `sx_measure_eye` sweeps vref for **one eye**, i.e. one
bit. The number wanted here is a **per-byte** vref that all 9 bits share, so
the sweep is driven from Tcl instead:

```
for each vref v in VREF_SWEEP:          # 0.05:0.25:0.005 -> 41 points
    for each bit in the byte:           # dq0..dq7 + dmi0
        aperture(bit, v)
    min_aper(v) = min over bits
vref_byte = argmax_v min_aper(v)        # widest worst-case eye
```

The bits are then re-measured at `vref_byte`, and those are the numbers that
reach the CSV. Each bit's eye object is created **once** and re-measured across
all 41 sweep points — rebuilding it per point would re-slice the waveform 41x.

## Layout

```
measure_eye.tcl      driver: glob -> sweep -> CSV -> replay script
lib/eye_lib.tcl      sweep parsing, signal naming, eye create/measure/query
lib/replay.tcl       replay + session script generation
cfg/lp5x_write.tcl   LP5x write setup (UI, phase, vac, sweep, pad prefixes, bits)
cfg/nand_read.tcl    NAND read setup — placeholder values, fill in before use
```

Adding a case = adding a file under `cfg/`. The driver is protocol-agnostic.

## Script runs, "Tcl script done", but the window is empty

`sx_create_eye` builds the eye **data object**; `sx_measure_eye` reads numbers
off it. Neither one puts a curve in a panel. Headless measurement needs
nothing more, which is why `-no_gui` works — but in the GUI the object is
created in memory and never drawn, so the script completes cleanly with a
blank window. Two calls are missing: open a panel, and plot the eye into it.

Their names vary by build, so `eye::probe` resolves them at run time from a
candidate list and `eye::ensure_window` / `eye::show` use whatever it found.
If it finds nothing it logs every plausible `sx_*` command in your build:

```
[eye] window command       : <not found>
[eye] plot command         : <not found>
[eye] ---- display command not resolved. candidates in this build: ----
[eye]     sx_add_curve
[eye]     sx_create_window
...
```

To get the full surface:

```sh
sx_sub -no_gui probe_ace.tcl            # list only, invokes nothing (safe)
sx_sub -no_gui probe_ace.tcl -usage     # also capture signature strings (noisy)
```

Worth running `-usage` from the GUI as well (**Run ACE script**): there the
graphical commands report their real signatures instead of refusing with
"batch mode".

`-usage` calls each candidate with no arguments inside `catch` and records the
error, because ACE reports these as usage strings:

```
sx_add_cursor(panel_obj,<xloc>,<option>.) ; argument type error
```

That error text *is* the signature — it is the only such documentation
available without SolvNet. Expect every probed command to report an error;
that is the mechanism working, not a failure. Everything goes to the output
file and nothing is acted on. Do not run `-usage` inside a flow that matters.

## What was verified

Against a stubbed ACE layer (`sx_*` replaced by a synthetic tent-shaped
aperture model), on Tcl 8.6:

- the flow runs clean end to end over multiple FSDBs;
- CSV header and column order match the required format exactly;
- the byte-level max-min vref search matches an independent brute-force
  recomputation, bit for bit;
- generated `.sx` files parse back with `pidx` contiguous and
  `pidx == ridx*grid_cols + cidx` throughout, and every panel's signal, trigger
  and `em_vref` match the CSV row for that fsdb;
- `session_scope all_fsdb` emits 3 `wdf` lines and 54 eye panels, 18 per
  `fidx`, each carrying its own file's per-byte vref;
- unit conversion: `-156.25p` -> `-1.5625e-10`, `625p` -> `6.25e-10`,
  `0.1375` -> `137.5m`, and values already in scientific notation pass through;
- swapping to `cfg/nand_read.tcl` changes bit count, CSV header, strobe naming
  and UI with no code change;
- against a fixture whose `eye_width` key is malformed, the generator leaves
  the token alone and warns rather than guessing;
- `attr=` comes out as `0:0:1:0 .. 7:7:1:0 8:0:1:0` for each byte, output
  carries no tabs, and `line src=` / `attr=` are spelled as in the format;
- changing a config's `eye_type` moves `eye_meas=` in the generated `.sx` with
  it, so the session's mask type cannot drift from the measurement's.

Every token of the LP5x reference panel is accounted for — 11 substituted, the
rest copied verbatim.

Also verified for the config-driven substitution: renaming `em_vref` to
`hex_vref` and `eye_meas` to `hex_mask` in both the reference panel and
`session_subst` carries the values through unchanged; leaving `session_subst`
pointing at a key the panel lacks reports it; and `.sx` output for the existing
configs is byte-identical to before the change.

The `sx_*` call signatures themselves are taken from the working script this
was built from and are unverified here.

`eye::show` does not know `sx_display_eye`'s argument order, so it tries
`(eye)`, `(window, eye)` and `(eye, window)` once, logs whichever succeeds, and
reuses only that form. All three paths plus the batch-mode skip are covered by
the stub tests.

The code requires **Tcl 8.5+** (`dict`, `lassign`, `apply`, `{*}`). Older
WaveView builds embed 8.4, where those are syntax errors; `lib/eye_lib.tcl`
checks `info patchlevel` up front and fails with a readable message rather than
a parse error. `out/ace_commands.txt` records the interpreter version.

## Notes on the original snippet

Carried over as fixes, in case they also exist in the source script:

- `sx_siganal` → `sx_signal`
- `set eye_value = [...]` — Tcl `set` takes no `=`
- `[expr [sx_query_eye] $eye1 "aper"]*1.0e12]` — brackets misplaced; should be
  `[expr {[sx_query_eye $eye "aper"] * 1.0e12}]`
- `set VREF_SWEEP : 0.05:...` — stray `:` (tolerated by the sweep parser anyway)
- `glob **.fsdb` → `*.fsdb`; `**` is not a recursive glob in Tcl
