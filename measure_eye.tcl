#!/usr/bin/env wv
# ===========================================================================
# measure_eye.tcl -- batch eye measurement for Synopsys Custom WaveView
#
#   sx_sub -no_gui measure_eye.tcl cfg/lp5x_write.tcl
#
# If the wrapper does not forward arguments, set the config via the
# environment instead:  DM_EYE_CFG=cfg/lp5x_write.tcl sx_sub -no_gui measure_eye.tcl
#
# For every *.fsdb in the working directory:
#   * sweep vref over the configured range,
#   * at each vref measure the aperture of all bits in a byte,
#   * pick the vref whose WORST bit aperture is largest (byte-level optimum),
#   * re-measure every bit at that vref and record it,
#   * emit one CSV row and one .sx session per fsdb.
#
# CSV columns:
#   fsdb, <byte0 bits...>, <byte1 bits...>, vref0, vref1
# ===========================================================================

set SCRIPT_DIR [file dirname [file normalize [info script]]]
source [file join $SCRIPT_DIR lib eye_lib.tcl]
source [file join $SCRIPT_DIR lib session.tcl]

# ---- config ---------------------------------------------------------------
# Resolved from, in order:
#   1. a DM_EYE_CFG variable set by a launcher script before sourcing this
#   2. argv -- the first entry that is a readable file other than this script
#   3. the DM_EYE_CFG environment variable
# There is deliberately NO default: sx_sub does not necessarily forward script
# arguments, and silently measuring with the wrong protocol is worse than
# stopping.  Accepts a path or a bare config name ("nand_read").
proc resolve_cfg {name} {
    global SCRIPT_DIR
    foreach cand [list $name [file join $SCRIPT_DIR $name] \
                       [file join $SCRIPT_DIR cfg $name] \
                       [file join $SCRIPT_DIR cfg $name.tcl]] {
        if {[file readable $cand] && ![file isdirectory $cand]} { return $cand }
    }
    return ""
}

proc cfg_from_argv {} {
    global SCRIPT_DIR
    if {![info exists ::argv]} { return "" }
    set self [file normalize [file join $SCRIPT_DIR measure_eye.tcl]]
    foreach a $::argv {
        if {[string match "-*" $a]} { continue }
        set r [resolve_cfg $a]
        if {$r eq "" || [file normalize $r] eq $self} { continue }
        return $r
    }
    return ""
}

eye::log "argv: [expr {[info exists ::argv] ? $::argv : {<unset>}}]"

set CFG_FILE ""
set CFG_FROM ""
if {[info exists ::DM_EYE_CFG] && $::DM_EYE_CFG ne ""} {
    set CFG_FILE [resolve_cfg $::DM_EYE_CFG]
    set CFG_FROM "DM_EYE_CFG variable"
}
if {$CFG_FILE eq ""} {
    set CFG_FILE [cfg_from_argv]
    if {$CFG_FILE ne ""} { set CFG_FROM "argv" }
}
if {$CFG_FILE eq "" && [info exists env(DM_EYE_CFG)] && $env(DM_EYE_CFG) ne ""} {
    set CFG_FILE [resolve_cfg $env(DM_EYE_CFG)]
    set CFG_FROM "DM_EYE_CFG environment variable"
    if {$CFG_FILE eq ""} {
        error "DM_EYE_CFG is set to '$env(DM_EYE_CFG)' but no such config was found"
    }
}

if {$CFG_FILE eq ""} {
    set avail {}
    foreach f [lsort [glob -nocomplain [file join $SCRIPT_DIR cfg *.tcl]]] {
        lappend avail [file rootname [file tail $f]]
    }
    error "no config selected -- refusing to guess.\n\
      available: [join $avail {, }]\n\
      If sx_sub forwards script arguments:\n\
    \    sx_sub -no_gui measure_eye.tcl cfg/nand_read.tcl\n\
      If it does not, use a launcher script (see run_example.tcl):\n\
    \    set DM_EYE_CFG nand_read\n\
    \    source measure_eye.tcl\n\
      or set the environment variable -- note csh/tcsh syntax:\n\
    \    setenv DM_EYE_CFG nand_read        (csh/tcsh)\n\
    \    export DM_EYE_CFG=nand_read        (bash/sh)"
}

if {![file readable $CFG_FILE]} { error "config not readable: $CFG_FILE" }
source $CFG_FILE
eye::log "config: $CFG_FILE ([dict get $CFG name]) -- via $CFG_FROM"

set OUT_DIR [file join $SCRIPT_DIR out]
file mkdir $OUT_DIR

# Panel templates are built in; a config may override them, or point
# session_template at a saved .sx to lift them from a real session.
if {[dict exists $CFG session_template]} {
    set TPL [dict get $CFG session_template]
    if {$TPL ne "" && [file pathtype $TPL] eq "relative"} {
        dict set CFG session_template [file join $SCRIPT_DIR $TPL]
    }
}

eye::probe
set SWEEP [eye::parse_sweep [dict get $CFG vref_sweep]]
eye::log "vref sweep: [llength $SWEEP] points, [lindex $SWEEP 0] .. [lindex $SWEEP end]"

# ---- CSV header -----------------------------------------------------------
set header [list fsdb]
foreach b [dict get $CFG byte_order] {
    foreach bit [dict get $CFG bytes $b bits] { lappend header $bit }
}
foreach b [dict get $CFG byte_order] { lappend header "vref$b" }

set csv_path [file join $OUT_DIR "[dict get $CFG name]_eye.csv"]
set csv [open $csv_path w]
puts $csv [join $header ,]
flush $csv

# ---- main loop ------------------------------------------------------------
set ALL_ENTRIES {}
set fsdb_list [lsort [glob -nocomplain [dict get $CFG fsdb_glob]]]
if {[llength $fsdb_list] == 0} {
    eye::log "WARNING: no files matched [dict get $CFG fsdb_glob] in [pwd]"
}

foreach fsdb $fsdb_list {
    eye::log "=== $fsdb"
    if {[catch {sx_open_sim_file_read $fsdb} fh]} {
        eye::log "open failed, skipping: $fh"
        continue
    }

    set results [dict create]
    foreach b [dict get $CFG byte_order] {
        set bits [dict get $CFG bytes $b bits]
        set trig [sx_signal [eye::strobe_sig $CFG $b]]

        # One eye object per bit, reused across all sweep points.
        set eyes [dict create]
        foreach bit $bits {
            dict set eyes $bit [eye::create \
                [eye::data_sig $CFG $b $bit] $trig \
                [dict get $CFG ui] [dict get $CFG eye_shift]]
        }

        lassign [eye::best_vref $CFG $eyes $bits $SWEEP] best_v best_min per_bit

        dict set results $b vref    [eye::fmt_vref $best_v]
        dict set results $b per_bit $per_bit
        eye::log "byte $b: vref=[eye::fmt_vref $best_v] min_eye=[eye::fmt_ps $best_min] ps"
    }

    # ---- CSV row ----------------------------------------------------------
    set row [list $fsdb]
    foreach b [dict get $CFG byte_order] {
        foreach bit [dict get $CFG bytes $b bits] {
            lappend row [eye::fmt_ps [dict get $results $b per_bit $bit]]
        }
    }
    foreach b [dict get $CFG byte_order] { lappend row [dict get $results $b vref] }
    puts $csv [join $row ,]
    flush $csv

    # ---- session ----------------------------------------------------------
    set stem [file rootname [file tail $fsdb]]
    lappend ALL_ENTRIES [list $fsdb $results]
    if {[dict get $CFG session_scope] eq "per_fsdb"} {
        sess::write \
            [file join $OUT_DIR "${stem}_[dict get $CFG name].sx"] \
            $CFG [list [list $fsdb $results]]
    }

    eye::close_file $fh
}

close $csv

# One combined .sx holding every fsdb as its own wdf index, when asked for.
if {[dict get $CFG session_scope] eq "all_fsdb" && [llength $ALL_ENTRIES]} {
    sess::write [file join $OUT_DIR "[dict get $CFG name].sx"] $CFG $ALL_ENTRIES
}

eye::log "done -> $csv_path"
