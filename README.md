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

The panel template is built in and the grid is derived from the bit count, so
a config change is the only thing needed — no file to keep in sync.

To look at a result: `sx_sub`, then open `out/corner_tt_1p0v_lp5x_write.sx`.

## Adding a protocol

A protocol is a file in `cfg/`. No code changes, no companion files.
`cfg/nand_read.tcl` is a filled-in skeleton to copy. What a config owns:

| | |
|---|---|
| `ui` `eye_shift` `vac` `vref_sweep` `eye_type` | timing and the measurement |
| `data_fmt` `strobe_fmt` | **signal naming**, as format strings |
| `byte_order` + `bytes` | how many bytes, which bits, which pad instance and strobe index — and the CSV column order |
| `grid_cols` `session_scope` | `.sx` layout and whether files are combined |
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

`cfg/lp5x_ca.tcl` is a skeleton for this, with the spots to fill marked.

## How the session is built

A session is a text file, and the measurement pass already knows every signal
name and the per-byte vref, so `lib/session.tcl` writes one from the config.

The panel line is a built-in template; per bit it substitutes:

| field | value |
|---|---|
| `pidx` / `ridx` / `cidx` | grid position, row-major over `grid_cols` |
| `eye_ext` | `<fidx>|<differential strobe>` for that byte |
| `em_vref` | the byte's measured vref |
| `eye_width` / `eye_shift` / `em_vac` | UI / phase / vac from the config |
| `eye_meas` | the config's `eye_type` — the same value passed to `sx_measure_eye type=` |
| `fidx` and `name=` on the `line` | source file index and the bit's signal |
| `attr=` on the `line` | trace colour, `<i>:<i mod attr_colors>:...`, cycled per byte |

Everything else in the template — `eye_plot`, `eye_edge`, `em_aper`, `sigtype`
and so on — is emitted as written.

### Grid

`grid_cols` columns (4), filled row-major, and the last row padded with empty
panels so the grid is rectangular. The bit count drives it:

| bits | panels | eyes per row |
|---|---|---|
| 8 | 8 | 4 + 4 |
| 10 | 12 | 4 + 4 + 2 |
| 18 | 20 | 4 + 4 + 4 + 4 + 2 |

### When the built-in panel is not the right one

A hexagonal-mask panel does not carry the same fields. Override the template
in the config:

```tcl
dict set CFG sx_panel [list \
  {  panel_begin eyediag pidx=0 ridx=0 cidx=0 hexmask=on eye_ext=0| hex_vref=0m ...} \
  {    line src=wdf lidx=0 fidx=0 "name=" attr=0:0:1:0 disp=show} \
  {  panel_end} ]
```

and point `session_subst` at whatever that panel calls each value.
`sx_header` / `sx_empty` / `sx_footer` override the other parts the same way.

Alternatively, lift the panel from a session saved out of the GUI — useful for
carrying over colours or mask settings tuned there:

```tcl
dict set CFG session_template ref/lp5x_ca.sx
```

That file is read only for its `panel_begin` / `line` / `panel_end` lines; the
same substitutions are applied to it.

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
measure_eye.tcl      driver: glob -> vref sweep -> CSV -> .sx
lib/eye_lib.tcl      sweep parsing, signal naming, eye create/measure/query
lib/session.tcl      .sx generation from a reference panel
cfg/lp5x_write.tcl   LP5x write
cfg/nand_read.tcl    NAND read     - template, fill in before use
cfg/lp5x_ca.tcl      LP5x CA       - hexagonal-mask skeleton, see below
```

Adding a case = a file under `cfg/`. The driver carries no protocol knowledge.

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

Also verified: the built-in template reproduces the earlier reference-file
output byte for byte; the grid fills 4+4+2 for 10 bits and stays rectangular
for 5/7/8/10/16/18 bits and at `grid_cols 3`; and a config that replaces
`sx_panel` wholesale with hexagonal-style field names carries every value
through.

For the config-driven substitution: renaming `em_vref` to
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
