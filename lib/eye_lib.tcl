# ---------------------------------------------------------------------------
# eye_lib.tcl -- shared helpers for Synopsys Custom WaveView (ACE) eye
#                measurement in -no_gui batch mode.
#
# Loaded by measure_eye.tcl.
# ---------------------------------------------------------------------------

# This code uses dict, lassign, apply and {*} -- all Tcl 8.5+.  WaveView
# embeds its own interpreter and older builds ship 8.4, where every one of
# those is a syntax error.  Fail with a readable message instead.
if {[package vcompare [info patchlevel] 8.5] < 0} {
    error "eye_lib requires Tcl 8.5+; this WaveView embeds [info patchlevel].\
           Report the version and the flow can be rewritten for 8.4."
}

namespace eval eye {
    variable CLOSE_FILE_CMD ""
    variable PROBED         0
}

# --------------------------------------------------------------------------
# Command probing.
#
# The exact spelling of the session-save and file-close entry points differs
# between WaveView releases, so resolve them at run time instead of hard
# coding a guess.  Run  `info commands sx_*`  in the WaveView Tcl console to
# see the full list for your build, then pin the right name here.
# --------------------------------------------------------------------------
proc eye::probe {} {
    variable CLOSE_FILE_CMD
    variable PROBED
    if {$PROBED} return
    foreach c {sx_close_sim_file sx_close_file sx_close} {
        if {[llength [info commands $c]] > 0} { set CLOSE_FILE_CMD $c; break }
    }
    eye::log "ACE build provides [llength [info commands sx_*]] sx_* commands"
    eye::log "close-file command : [expr {$CLOSE_FILE_CMD eq {} ? {<not found>} : $CLOSE_FILE_CMD}]"
    set PROBED 1
}

proc eye::log {msg} {
    puts stderr "\[eye\] $msg"
}

# --------------------------------------------------------------------------
# Sweep spec "start:stop:step"  ->  list of values.
# Leading ':' (as in ": 0.05:0.25:0.005") is tolerated.
# --------------------------------------------------------------------------
proc eye::parse_sweep {spec} {
    set spec [string trim [string trim $spec] :]
    lassign [split $spec :] start stop step
    if {$start eq "" || $stop eq "" || $step eq "" || $step == 0} {
        error "bad sweep spec '$spec' (expected start:stop:step)"
    }
    set n   [expr {int(round((double($stop) - double($start)) / double($step)))}]
    set out {}
    for {set k 0} {$k <= $n} {incr k} {
        lappend out [expr {double($start) + $k * double($step)}]
    }
    return $out
}

# --------------------------------------------------------------------------
# Signal name builders.  Adjust here if your netlist naming differs -- this is
# the single place the whole flow derives signal names from.
# --------------------------------------------------------------------------
# Signal names come from format strings in the config, not from this code,
# so a new protocol is a new cfg/ file rather than a code change.
#   %prefix%  the byte's pad_prefix      %bit%   the bit name (dq0, dmi1, ...)
#   %pdqs% %ndqs%  strobe p/n roots      %idx%   the byte's strobe index
proc eye::render {fmt cfg b {bit ""}} {
    set bc [dict get $cfg bytes $b]
    return [string map [list \
        %prefix% [dict get $bc pad_prefix] \
        %bit%    $bit \
        %idx%    [dict get $bc dqs_idx] \
        %pdqs%   [dict get $cfg pdqs] \
        %ndqs%   [dict get $cfg ndqs] \
    ] $fmt]
}

proc eye::data_sig   {cfg b bit} { return [eye::render [dict get $cfg data_fmt]   $cfg $b $bit] }
proc eye::strobe_sig {cfg b}     { return [eye::render [dict get $cfg strobe_fmt] $cfg $b] }

# Which wdf index a byte's signals live in (multi-fsdb sessions).
proc eye::byte_fidx {cfg b} {
    set bc [dict get $cfg bytes $b]
    if {[dict exists $bc fidx]} { return [dict get $bc fidx] }
    return 0
}

# --------------------------------------------------------------------------
# Eye construction.  One eye object per bit, created once and re-measured at
# many vref values -- creating it per vref would re-slice the waveform 41x.
# --------------------------------------------------------------------------
proc eye::create {data_sig_name trig_sig ui phase {trig_edge BOTH} {trig_level 0.00}} {
    set sig [sx_signal $data_sig_name]
    return [sx_create_eye $sig \
                "ui=${ui}" \
                "trigger=EXT" \
                "phase=${phase}" \
                "trigsig=${trig_sig}" \
                "trig_edge=${trig_edge}" \
                "trig_level=${trig_level}"]
}

# The sx_measure_eye argument list comes from the config, because it is not
# the same for every mask: a rectangular ddr4 measurement and a hexagonal one
# do not take the same fields.  Placeholders: %type% %vref% %vac% %ui% %shift%
proc eye::measure_args {cfg vref} {
    return [string map [list \
        %type%  [dict get $cfg eye_type] \
        %vref%  $vref \
        %vac%   [dict get $cfg vac] \
        %ui%    [dict get $cfg ui] \
        %shift% [dict get $cfg eye_shift] \
    ] [dict get $cfg measure_args]]
}

# Measure one eye at a FIXED vref (no built-in vref sweep -- the byte-level
# sweep is driven by eye::best_vref below) and return the aperture in ps.
# A closed eye / failed measurement yields 0.0 so it loses the max-min search.
proc eye::aperture_ps {cfg eye vref} {
    if {[catch {sx_measure_eye $eye {*}[eye::measure_args $cfg $vref]} err]} {
        eye::log "measure failed (vref=$vref): $err"
        return 0.0
    }
    if {[catch {sx_query_eye $eye "aper"} ap]} {
        eye::log "query aper failed (vref=$vref): $ap"
        return 0.0
    }
    if {![string is double -strict $ap]} { return 0.0 }
    return [expr {double($ap) * 1.0e12}]
}

proc eye::vcent {eye} {
    if {[catch {sx_query_eye $eye "vcent"} v]} { return "" }
    if {![string is double -strict $v]} { return "" }
    return $v
}

# --------------------------------------------------------------------------
# Byte-level vref optimisation.
#
#   eyes   : dict  bit -> eye handle
#   order  : list of bits, defines column order in the result
#
# Returns: {best_vref best_min_ps {bit ps bit ps ...}}
# best vref = the sweep point whose WORST bit aperture is the largest.
# --------------------------------------------------------------------------
proc eye::best_vref {cfg eyes order sweep} {
    set best_v   ""
    set best_min -1.0

    foreach v $sweep {
        set worst ""
        foreach bit $order {
            set ap [eye::aperture_ps $cfg [dict get $eyes $bit] $v]
            if {$worst eq "" || $ap < $worst} { set worst $ap }
        }
        if {$worst > $best_min} {
            set best_min $worst
            set best_v   $v
        }
    }

    # Re-measure at the winning vref so the per-bit numbers we report are the
    # ones actually taken at that operating point.
    set per_bit [dict create]
    foreach bit $order {
        dict set per_bit $bit [eye::aperture_ps $cfg [dict get $eyes $bit] $best_v]
    }
    return [list $best_v $best_min $per_bit]
}

proc eye::fmt_ps   {x} { return [format "%.2f" $x] }
proc eye::fmt_vref {x} { return [format "%.3f" $x] }

proc eye::close_file {handle} {
    variable CLOSE_FILE_CMD
    eye::probe
    if {$CLOSE_FILE_CMD eq ""} { return }
    catch {$CLOSE_FILE_CMD $handle}
}
