# Reference session

Put a **real** WaveView session here as `waveview.session`:

1. `sx_sub` to open the GUI
2. open one fsdb, build one eye the way you want it
3. File -> Save Session
4. copy the saved file to `ref/waveview.session`

`lib/session.tcl` reuses that file's `panel_begin` / `line` / `panel_end` lines
verbatim and substitutes only the fields that must vary per bit (`pidx`,
`ridx`, `cidx`, `eye_ext`, `em_vref`, `eye_width`, `eye_shift`, `em_vac`, and
the signal `name`). Everything else is copied byte for byte, so tokens this
code does not understand are preserved.

If a key it wants to substitute is absent it says so on startup rather than
silently emitting the reference value:

```
[eye] WARNING: 'eye_width=' not found in the reference session panel.
[eye] WARNING: UI will keep the reference value instead.
```

Without `waveview.session` the measurement still runs; session writing is
skipped.

`sample_from_chat.session` is a hand-transcribed excerpt used only to test the
generator. It contains transcription typos (`eye_width-312.5p` with a hyphen,
`eye_mase`, `eye_alvl=sigle`, `eye_plot=flod`, `npose`, and an `eye_shift`
exponent that disagrees with the UI). **Do not use it as the template.**
