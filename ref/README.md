# Reference sessions

`lp5x_write.sx` and `nand_read.sx` are the panel templates the generator reads.
They ship ready to use — **nothing needs to be supplied to run the flow.**

They were built from the session format as given: header (`wdf`,
`scalar list`, `waveview_begin`), one `eye_plot=fold` eyediag panel with its
`line`, an empty panel for grid padding, and `waveview_end` / `browserOpened`.
`nand_read.sx` is the same structure with NAND timing and `pdqs`/`ndqs` naming.

`lib/session.tcl` reuses the `panel_begin` / `line` / `panel_end` lines
verbatim and substitutes only what must vary per bit:

| field | value |
|---|---|
| `pidx` / `ridx` / `cidx` | grid position, row-major over `grid_cols` |
| `eye_ext` | `<fidx>|<differential strobe>` |
| `em_vref` | that byte's measured vref |
| `eye_width` / `eye_shift` / `em_vac` | UI / phase / vac from the config |
| `fidx` and `name=` on the `line` | source file index and the bit's signal |
| `attr=` on the `line` | trace colour, cycled per bit (see below) |
| `eye_meas` | the config's `eye_type`, same value as `sx_measure_eye type=` |

Everything else is copied byte for byte, so mask settings and any token this
code does not model pass straight through.

Indentation is two spaces per level (`panel_begin` at 2, `line` at 4), not
tabs.

## Trace colour

`attr=` cycles per bit within a byte and restarts at each byte, so byte 0 and
byte 1 share the palette:

```
0:0:1:0  1:1:1:0  2:2:1:0 ... 7:7:1:0  8:0:1:0
```

First field is the bit's index in its byte, second the colour, wrapping at
`attr_colors` (8). The remaining fields are carried from the reference, so
changing `1:0` there changes it everywhere.

Every token in the panel is accounted for: 11 substituted, the rest copied.

## Replacing a reference

Anything tuned in the GUI — colours, mask display, axis settings — can be
baked in by saving a session over the file:

1. `sx_sub`, open one fsdb, build one eye the way you want that protocol to look
2. File -> Save Session
3. save over `ref/<config name>.sx`

The generator picks it up with no other change, and reports any key it can no
longer substitute:

```
[eye] WARNING: 'eye_width=' not found in the reference .sx panel.
[eye] WARNING: UI will keep the reference value instead.
```

## samples/

Test fixtures, not templates. `lp5x_write.sample.sx` is the same content as
`lp5x_write.sx`; `broken_key.sample.sx` has `eye_width` deliberately malformed
to exercise the warning path.
