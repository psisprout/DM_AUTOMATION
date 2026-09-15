# ---------------------------------------------------------------------------
# cfg/nand_read.tcl -- NAND READ
#
# TEMPLATE.  Timing, naming and the reference .sx below are placeholders
# carried over from the LP5x flow -- replace them with the NAND values.
# Nothing outside this file and ref/nand_read.sx needs to change.
# ---------------------------------------------------------------------------

set UI_VALUE     625p            ;# <-- NAND UI
set EYE_SHIFT    -312.5p         ;# <-- usually -UI/2
set VAC_VALUE    25m
set VREF_SWEEP   0.05:0.25:0.005
set EYE_TYPE     ddr4            ;# <-- confirm the mask type for NAND

set PAD_PREFIX1  rcv1_pad
set NDQS         ndqs
set PDQS         pdqs

set BYTE0_BITS   {dq0 dq1 dq2 dq3 dq4 dq5 dq6 dq7}

set FSDB_GLOB    *.fsdb

# ---------------------------------------------------------------------------
set CFG [dict create \
    name        nand_read \
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

dict set CFG session_template ref/nand_read.sx
dict set CFG grid_cols        4
dict set CFG session_scope    per_fsdb

dict set CFG session_fmt [dict create \
    eye_width sci   \
    eye_shift sci   \
    em_vac    milli \
    em_vref   milli \
]

dict set CFG byte_order {0}
dict set CFG bytes 0 [dict create pad_prefix $PAD_PREFIX1 dqs_idx 0 bits $BYTE0_BITS]
