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

## Notes on the original snippet

Carried over as fixes, in case they also exist in the source script:

- `sx_siganal` → `sx_signal`
- `set eye_value = [...]` — Tcl `set` takes no `=`
- `[expr [sx_query_eye] $eye1 "aper"]*1.0e12]` — brackets misplaced; should be
  `[expr {[sx_query_eye $eye "aper"] * 1.0e12}]`
- `set VREF_SWEEP : 0.05:...` — stray `:` (tolerated by the sweep parser anyway)
- `glob **.fsdb` → `*.fsdb`; `**` is not a recursive glob in Tcl
