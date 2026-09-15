# DM_AUTOMATION

Batch eye measurement for Synopsys Custom WaveView (ACE Tcl), run headless with
`-no_gui`, producing both a CSV summary and a re-openable WaveView session per
FSDB.

## Run

```sh
wv -no_gui measure_eye.tcl cfg/lp5x_write.tcl     # LP5x write
wv -no_gui measure_eye.tcl cfg/nand_read.tcl      # NAND read (template)
```

Run it from the directory holding the `.fsdb` files. Outputs land in `out/`:

| file | contents |
|---|---|
| `out/<cfg>_eye.csv` | `fsdb,dq0..dq7,dmi0,dq8..dq15,dmi1,vref0,vref1` |
| `out/<fsdb>_<cfg>.replay.tcl` | standalone script that redraws the same eyes |
| `out/<fsdb>_<cfg>.replay.session` | native session, written when the replay runs in the GUI |

To look at a result: `wv out/corner_tt_1p0v_lp5x_write.replay.tcl`

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
wv -no_gui probe_ace.tcl            # list only, invokes nothing (safe)
wv -no_gui probe_ace.tcl -usage     # also capture signature strings (noisy)
```

`-usage` calls each candidate with no arguments inside `catch` and records the
error, because ACE reports these as usage strings:

```
sx_add_cursor(panel_obj,<xloc>,<option>.) ; argument type error
```

That error text *is* the signature — it is the only such documentation
available without SolvNet. Expect every probed command to report an error;
that is the mechanism working, not a failure. Everything goes to the output
file and nothing is acted on. Do not run `-usage` inside a flow that matters.

### Display only works in the GUI

`sx_display_eye` exists on this build but refuses under `-no_gui`:

```
sx_display_eye : can't perform graphical command in batch mode
```

So the measurement pass never draws, by design, and the replay script draws
only when opened in the GUI. `eye::batch_error` recognises that message once
and skips the remaining display calls rather than emitting one error per bit.
Measurement is unaffected either way.

Then pin the correct names in the two `foreach` candidate lists at the top of
`eye::probe` in `lib/eye_lib.tcl`. Nothing else has to change — the generated
replay scripts already call `eye::ensure_window` before creating any eye and
`eye::show` after each measurement.

The window is opened **before** the first `sx_create_eye` on purpose: if a
build attaches new eyes to the current window at creation time, opening it
afterwards draws nothing.

## Confirm these against your WaveView build

Three things could not be verified here (no WaveView install in this
environment), so they are handled defensively rather than hard-coded:

1. **Session-save command name.** `eye::probe` looks for `sx_save_session`,
   `sx_session_save`, `sx_save_session_file`, `sx_session` and uses whichever
   exists, logging the choice. Get the real list with

   ```tcl
   info commands sx_*
   ```

   in the WaveView Tcl console, then pin the name in `lib/eye_lib.tcl`.
   Same for the file-close command.

2. **Saving a session from `-no_gui`.** With no windows or panels open, a
   native session written headlessly may carry no layout. That is why the
   replay script is the primary artifact — it reconstructs the eyes from
   scratch and calls the native writer only once it is running in the GUI,
   where the layout exists. If your build does save a usable session
   headlessly, add an `eye::save_session` call at the end of the per-FSDB loop
   in `measure_eye.tcl` and the replay script becomes a redundant backup.

3. **Signal naming.** Derived in one place, `eye::data_signal_name` and
   `eye::strobe_signal_name`:

   - data   `v(<pad_prefix>_<bit>)` → `v(rcv1_pad_dq0)`
   - strobe `v(<pad_prefix>_<pdqs><idx>,<pad_prefix>_<ndqs><idx>)` → `v(rcv1_pad_pwck0,rcv1_pad_nwck0)`

   The strobe is read as a **differential pair** (p and n). The original
   snippet had `PDQS` in both halves, which looked like a typo for p/n; adjust
   those two procs if the real naming differs.

## What was verified

Against a stubbed ACE layer (`sx_*` replaced by a synthetic tent-shaped
aperture model), on Tcl 8.6:

- the flow runs clean end to end over multiple FSDBs;
- CSV header and column order match the required format exactly;
- the byte-level max-min vref search matches an independent brute-force
  recomputation, bit for bit;
- generated replay scripts execute and reproduce the CSV apertures exactly.

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
