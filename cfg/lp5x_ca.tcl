# ---------------------------------------------------------------------------
# cfg/lp5x_ca.tcl -- LP5x CA, hexagonal mask
#
# SKELETON.  A hexagonal-mask measurement does not take the same arguments as
# the rectangular ddr4 one, and its .sx panel does not carry the same fields.
# Neither is guessed here -- both are config data, marked <-- below.
#
# To finish it:
#   1. build one CA eye with the hexagonal mask in the GUI, save the session
#      over ref/lp5x_ca.sx
#   2. set measure_args to the argument list sx_measure_eye wants for it
#   3. point session_subst at whatever that panel calls vref / vac / UI / shift
#      (diff ref/lp5x_ca.sx against ref/lp5x_write.sx to see what changed)
#
# No code changes are needed for either.
# ---------------------------------------------------------------------------

set UI_VALUE     312.5p
set EYE_SHIFT    -156.25p
set VAC_VALUE    25m
set VREF_SWEEP   0.05:0.25:0.005
set EYE_TYPE     hexagonal        ;# <-- the mask token this build uses

set PAD_PREFIX1  rcv1_pad
set NDQS         nck
set PDQS         pck

set CA_BITS      {ca0 ca1 ca2 ca3 ca4 ca5 ca6}   ;# <-- the real CA bit list

set FSDB_GLOB    *.fsdb

# ---------------------------------------------------------------------------
set CFG [dict create \
    name        lp5x_ca \
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

dict set CFG data_fmt   {v(%prefix%_%bit%)}
dict set CFG strobe_fmt {v(%prefix%_%pdqs%%idx%,%prefix%_%ndqs%%idx%)}

# Panel template: built in.  Uncomment to lift it from a saved .sx instead,
# or override sx_panel / sx_header / sx_empty / sx_footer directly.
# dict set CFG session_template ref/lp5x_ca.sx
dict set CFG grid_cols        4
dict set CFG attr_colors      8
dict set CFG session_scope    per_fsdb

dict set CFG session_fmt [dict create \
    eye_width sci   \
    eye_shift sci   \
    em_vac    milli \
    em_vref   milli \
]

# <-- if the hexagonal panel names these differently, rename the keys here;
#     a key that is not in the panel is reported at startup, not silently
#     dropped.
dict set CFG session_subst [dict create \
    eye_ext   trig        \
    em_vref   vref        \
    em_vac    cfg:vac     \
    eye_width cfg:ui      \
    eye_shift cfg:eye_shift \
    eye_meas  cfg:eye_type  \
    fidx      fidx        \
    name      sig         \
    attr      attr        \
]

# <-- the argument list sx_measure_eye takes for a hexagonal mask.
#     Placeholders: %type% %vref% %vac% %ui% %shift%
dict set CFG measure_args {type=%type% vref=%vref% vac=%vac%}

dict set CFG byte_order {0}
dict set CFG bytes 0 [dict create pad_prefix $PAD_PREFIX1 dqs_idx 0 bits $CA_BITS]
