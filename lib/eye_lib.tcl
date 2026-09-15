# ---------------------------------------------------------------------------
# eye_lib.tcl -- shared helpers for Synopsys Custom WaveView (ACE) eye
#                measurement in -no_gui batch mode.
#
# Loaded by measure_eye.tcl and by every generated replay/session script.
# ---------------------------------------------------------------------------

namespace eval eye {
    variable SAVE_SESSION_CMD ""
    variable CLOSE_FILE_CMD   ""
    variable PROBED           0
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
    variable SAVE_SESSION_CMD
    variable CLOSE_FILE_CMD
    variable PROBED
    if {$PROBED} return

    foreach c {sx_save_session sx_session_save sx_save_session_file sx_session} {
        if {[llength [info commands $c]] > 0} { set SAVE_SESSION_CMD $c; break }
    }
    foreach c {sx_close_sim_file sx_close_file sx_close} {
        if {[llength [info commands $c]] > 0} { set CLOSE_FILE_CMD $c; break }
    }

    eye::log "ACE build provides [llength [info commands sx_*]] sx_* commands"
    eye::log "session-save command : [expr {$SAVE_SESSION_CMD eq {} ? {<not found>} : $SAVE_SESSION_CMD}]"
    eye::log "close-file command   : [expr {$CLOSE_FILE_CMD   eq {} ? {<not found>} : $CLOSE_FILE_CMD}]"
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
proc eye::data_signal_name {pad_prefix bit} {
    return "v(${pad_prefix}_${bit})"
}

# Differential strobe: v(<prefix>_<pdqs><idx>, <prefix>_<ndqs><idx>)
proc eye::strobe_signal_name {pad_prefix pdqs ndqs idx} {
    return "v(${pad_prefix}_${pdqs}${idx},${pad_prefix}_${ndqs}${idx})"
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

# Measure one eye at a FIXED vref (no ddr4_vref_sweep -- the byte-level sweep
# is driven by eye::best_vref below) and return the aperture in ps.
# A closed eye / failed measurement yields 0.0 so it loses the max-min search.
proc eye::aperture_ps {eye type vref vac} {
    if {[catch {sx_measure_eye $eye type=$type vref=$vref vac=$vac} err]} {
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
proc eye::best_vref {eyes order sweep type vac} {
    set best_v   ""
    set best_min -1.0

    foreach v $sweep {
        set worst ""
        foreach bit $order {
            set ap [eye::aperture_ps [dict get $eyes $bit] $type $v $vac]
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
        dict set per_bit $bit [eye::aperture_ps [dict get $eyes $bit] $type $best_v $vac]
    }
    return [list $best_v $best_min $per_bit]
}

proc eye::fmt_ps   {x} { return [format "%.2f" $x] }
proc eye::fmt_vref {x} { return [format "%.3f" $x] }

# --------------------------------------------------------------------------
# Session save.  Tries the native ACE session writer; callers should ALWAYS
# also emit a replay script (see eye::write_replay) because a native session
# written from -no_gui may carry no window/panel layout.
# --------------------------------------------------------------------------
proc eye::save_session {path} {
    variable SAVE_SESSION_CMD
    eye::probe
    if {$SAVE_SESSION_CMD eq ""} {
        eye::log "no session-save command in this build; replay script only"
        return 0
    }
    if {[catch {$SAVE_SESSION_CMD $path} err]} {
        eye::log "$SAVE_SESSION_CMD '$path' failed: $err"
        return 0
    }
    eye::log "session saved: $path"
    return 1
}

proc eye::close_file {handle} {
    variable CLOSE_FILE_CMD
    eye::probe
    if {$CLOSE_FILE_CMD eq ""} { return }
    catch {$CLOSE_FILE_CMD $handle}
}
