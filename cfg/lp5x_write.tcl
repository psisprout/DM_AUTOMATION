# ---------------------------------------------------------------------------
# cfg/lp5x_write.tcl -- LP5x WRITE eye measurement setup
#
# Everything site-specific lives here.  measure_eye.tcl reads the CFG dict
# built at the bottom and needs nothing else.
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
    byte_order  {0 1} \
]

# Per byte: which pad instance it sits on, which strobe index it is clocked
# by, and its bit order (which is also the CSV column order).
dict set CFG bytes 0 [dict create pad_prefix $PAD_PREFIX1 dqs_idx 0 bits $BYTE0_BITS]
dict set CFG bytes 1 [dict create pad_prefix $PAD_PREFIX2 dqs_idx 1 bits $BYTE1_BITS]
