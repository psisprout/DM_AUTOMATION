# ---------------------------------------------------------------------------
# cfg/lp5x_write.tcl -- LP5x WRITE
#
# A protocol is defined entirely by a file like this one plus its own
# reference .sx.  measure_eye.tcl and lib/ contain no protocol knowledge.
# ---------------------------------------------------------------------------

set UI_VALUE     312.5p
set EYE_SHIFT    -156.25p
set VAC_VALUE    25m
set VREF_SWEEP   0.05:0.25:0.005
set EYE_TYPE     ddr4

set PAD_PREFIX1  rcv1_pad
set PAD_PREFIX2  rcv2_pad
set NDQS         nwck
set PDQS         pwck

set BYTE0_BITS   {dq0 dq1 dq2 dq3 dq4 dq5 dq6 dq7 dmi0}
set BYTE1_BITS   {dq8 dq9 dq10 dq11 dq12 dq13 dq14 dq15 dmi1}

set FSDB_GLOB    *.fsdb

# ---------------------------------------------------------------------------
set CFG [dict create \
    name        lp5x_write \
    ui          $UI_VALUE \
    eye_shift   $EYE_SHIFT \
    vac         $VAC_VALUE \
    vref_sweep  $VREF_SWEEP \
    eye_type    $EYE_TYPE \
    pdqs        $PDQS \
    ndqs        $NDQS \
    fsdb_glob   $FSDB_GLOB \
    lib_relpath ../lib \
]

# --- signal naming ----------------------------------------------------------
# %prefix% = the byte's pad_prefix, %bit% = dq0/dmi1/..., %idx% = strobe index,
# %pdqs%/%ndqs% = strobe p/n roots.  Change these, not the code, for a
# different netlist convention.
dict set CFG data_fmt   {v(%prefix%_%bit%)}
dict set CFG strobe_fmt {v(%prefix%_%pdqs%%idx%,%prefix%_%ndqs%%idx%)}

# --- session output ---------------------------------------------------------
dict set CFG session_template ref/lp5x_write.sx
dict set CFG grid_cols        4
# attr= trace colour cycles 0..N-1 per byte
dict set CFG attr_colors      8
# per_fsdb : one .sx per fsdb (wdf 0)
# all_fsdb : one .sx holding every fsdb as wdf 0,1,2... with matching fidx
dict set CFG session_scope    per_fsdb

# The .sx format is not uniform: times are scientific notation, voltages keep
# an SI suffix.  Config values are SPICE style, so say how each field is
# written.  'raw' (the default for anything unlisted) copies verbatim.
dict set CFG session_fmt [dict create \
    eye_width sci   \
    eye_shift sci   \
    em_vac    milli \
    em_vref   milli \
]

# --- bytes ------------------------------------------------------------------
# Per byte: pad instance, strobe index, bit order (also the CSV column order),
# and optionally fidx -- which wdf index its signals come from, for sessions
# built from several fsdb files.
dict set CFG byte_order {0 1}
dict set CFG bytes 0 [dict create pad_prefix $PAD_PREFIX1 dqs_idx 0 bits $BYTE0_BITS]
dict set CFG bytes 1 [dict create pad_prefix $PAD_PREFIX2 dqs_idx 1 bits $BYTE1_BITS]
