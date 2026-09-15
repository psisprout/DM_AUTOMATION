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
#   * emit one CSV row and one replay/session script per fsdb.
#
# CSV columns:
#   fsdb, <byte0 bits...>, <byte1 bits...>, vref0, vref1
# ===========================================================================

set SCRIPT_DIR [file dirname [file normalize [info script]]]
source [file join $SCRIPT_DIR lib eye_lib.tcl]
source [file join $SCRIPT_DIR lib replay.tcl]

# ---- config ---------------------------------------------------------------
# sx_sub is a site wrapper and how it forwards argv is not guaranteed, so pick
# the first argument that is actually a readable file other than this script,
# rather than trusting position.
proc cfg_from_argv {} {
    if {![info exists ::argv]} { return "" }
    set self [file normalize [info script]]
    foreach a $::argv {
        if {[string match "-*" $a]}              { continue }
        if {![file readable $a]}                 { continue }
        if {[file normalize $a] eq $self}        { continue }
        return $a
    }
    return ""
}

if {[set _c [cfg_from_argv]] ne ""} {
    set CFG_FILE $_c
} elseif {[info exists env(DM_EYE_CFG)]} {
    set CFG_FILE $env(DM_EYE_CFG)
} else {
    set CFG_FILE [file join $SCRIPT_DIR cfg lp5x_write.tcl]
}
if {![file readable $CFG_FILE]} { error "config not readable: $CFG_FILE" }
source $CFG_FILE
eye::log "config: $CFG_FILE ([dict get $CFG name])"

set OUT_DIR [file join $SCRIPT_DIR out]
file mkdir $OUT_DIR

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
        set bc     [dict get $CFG bytes $b]
        set prefix [dict get $bc pad_prefix]
        set bits   [dict get $bc bits]

        set strobe [eye::strobe_signal_name $prefix \
                        [dict get $CFG pdqs] [dict get $CFG ndqs] [dict get $bc dqs_idx]]
        set trig   [sx_signal $strobe]

        # One eye object per bit, reused across all sweep points.
        set eyes [dict create]
        foreach bit $bits {
            dict set eyes $bit [eye::create \
                [eye::data_signal_name $prefix $bit] $trig \
                [dict get $CFG ui] [dict get $CFG eye_shift]]
        }

        lassign [eye::best_vref $eyes $bits $SWEEP \
                    [dict get $CFG eye_type] [dict get $CFG vac]] \
                best_v best_min per_bit

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

    # ---- replay / session -------------------------------------------------
    set stem [file rootname [file tail $fsdb]]
    eye::write_replay \
        [file join $OUT_DIR "${stem}_[dict get $CFG name].replay.tcl"] \
        $CFG $fsdb $results

    eye::close_file $fh
}

close $csv
eye::log "done -> $csv_path"
