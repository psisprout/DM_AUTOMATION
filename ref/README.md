# Reference sessions

Each protocol needs its own reference `.sx` here, named by the config's
`session_template` — `ref/lp5x_write.sx`, `ref/nand_read.sx`, and so on.

To make one:

1. `sx_sub` to open the GUI
2. open one fsdb and build **one eye** the way you want that protocol to look
   (mask, plot mode, trigger, edge, colours — all of it)
3. File -> Save Session
4. save it here under the name the config expects

`lib/session.tcl` reuses that file's `panel_begin` / `line` / `panel_end`
lines verbatim and substitutes only what must vary per bit:

| field | value |
|---|---|
| `pidx` / `ridx` / `cidx` | grid position, row-major over `grid_cols` |
| `eye_ext` | `<fidx>|<differential strobe>` |
| `em_vref` | that byte's measured vref |
| `eye_width` / `eye_shift` / `em_vac` | UI / phase / vac from the config |
| `fidx` and `name=` on the `line` | source file index and the bit's signal |

Everything else is copied byte for byte, so mask settings, colours and any
token this code does not model survive untouched. That is the point: a new
protocol is a new cfg/ file plus a reference `.sx`, not a code change.

If a key it means to substitute is absent it says so rather than silently
emitting the reference value:

```
[eye] WARNING: 'eye_width=' not found in the reference .sx panel.
[eye] WARNING: UI will keep the reference value instead.
```

Without the reference the measurement still runs and only `.sx` output is
skipped.

## samples/

Test fixtures only — **do not point a config at these.**

- `lp5x_write.sample.sx` — hand-transcribed from a real session. Believed
  correct after corrections, but `eye_mase=ddr4` was never confirmed and the
  panel set is abridged.
- `broken_key.sample.sx` — the same file with `eye_width` deliberately
  malformed, to exercise the warning path.
