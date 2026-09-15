# ---------------------------------------------------------------------------
# eye_lib.tcl -- shared helpers for Synopsys Custom WaveView (ACE) eye
#                measurement in -no_gui batch mode.
#
# Loaded by measure_eye.tcl and by every generated replay/session script.
# ---------------------------------------------------------------------------

# This code uses dict, lassign, apply and {*} -- all Tcl 8.5+.  WaveView
# embeds its own interpreter and older builds ship 8.4, where every one of
# those is a syntax error.  Fail with a readable message instead.
if {[package vcompare [info patchlevel] 8.5] < 0} {
    error "eye_lib requires Tcl 8.5+; this WaveView embeds [info patchlevel].\
           Report the version and the flow can be rewritten for 8.4."
}

namespace eval eye {
    variable SAVE_SESSION_CMD ""
    variable CLOSE_FILE_CMD   ""
    variable WINDOW_CMD       ""
    variable PLOT_CMD         ""
    variable CUR_WIN          ""
    variable BATCH            0
    variable PLOT_FORM        ""
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
    variable WINDOW_CMD
    variable PLOT_CMD
    variable PROBED
    if {$PROBED} return

    foreach c {sx_save_session sx_session_save sx_save_session_file sx_session} {
        if {[llength [info commands $c]] > 0} { set SAVE_SESSION_CMD $c; break }
    }
    foreach c {sx_close_sim_file sx_close_file sx_close} {
        if {[llength [info commands $c]] > 0} { set CLOSE_FILE_CMD $c; break }
    }

    # Display layer.  sx_create_eye only builds the data object -- in the GUI
    # something still has to open a panel and put the curve in it, or the
    # script completes with an empty window.  Eye-specific plotters first.
    # sx_new_panel is what this build has; called bare it yields the default
    # XY panel, and an eye will not render there -- eyes need an eye-diagram
    # panel.  eye::ensure_window asks for the type explicitly.
    foreach c {sx_new_panel sx_create_panel sx_add_panel
               sx_create_window sx_open_window sx_new_window} {
        if {[llength [info commands $c]] > 0} { set WINDOW_CMD $c; break }
    }
    # sx_display_eye confirmed present on this build (F-2011.09 era ACE); it
    # refuses to run under -no_gui with "can't perform graphical command in
    # batch mode", so display only ever happens in the GUI.
    foreach c {sx_display_eye sx_plot_eye sx_show_eye sx_add_eye sx_draw_eye
               sx_plot sx_display sx_add_curve sx_add_signal sx_show} {
        if {[llength [info commands $c]] > 0} { set PLOT_CMD $c; break }
    }

    eye::log "ACE build provides [llength [info commands sx_*]] sx_* commands"
    eye::log "session-save command : [expr {$SAVE_SESSION_CMD eq {} ? {<not found>} : $SAVE_SESSION_CMD}]"
    eye::log "close-file command   : [expr {$CLOSE_FILE_CMD   eq {} ? {<not found>} : $CLOSE_FILE_CMD}]"
    eye::log "window command       : [expr {$WINDOW_CMD       eq {} ? {<not found>} : $WINDOW_CMD}]"
    eye::log "plot command         : [expr {$PLOT_CMD         eq {} ? {<not found>} : $PLOT_CMD}]"
    set PROBED 1
    if {$WINDOW_CMD eq "" || $PLOT_CMD eq ""} { eye::display_hints }
}

# When the display commands could not be resolved, print every plausible
# candidate this build does have, so the right names can be pinned rather
# than guessed.  Run probe_ace.tcl for the full dump.
proc eye::display_hints {} {
    set hits {}
    foreach c [lsort [info commands sx_*]] {
        if {[regexp {plot|display|show|draw|add|window|panel|curve|graph|wave} $c]} {
            lappend hits $c
        }
    }
    eye::log "---- display command not resolved. candidates in this build: ----"
    foreach c $hits { eye::log "    $c" }
    eye::log "---- pin the right ones in eye::probe (lib/eye_lib.tcl) ----"
}

# Open (once) the window the eyes get drawn into.  Call BEFORE eye::create:
# some builds attach a new eye to the current window at creation time.
proc eye::ensure_window {{title "eye"}} {
    variable WINDOW_CMD
    variable CUR_WIN
    variable BATCH
    eye::probe
    if {$CUR_WIN ne "" || $BATCH} { return $CUR_WIN }
    if {$WINDOW_CMD eq ""} { return "" }

    # An eye only renders in an eye-diagram panel, not the default XY panel,
    # so ask for the type up front.  The exact type token is not documented
    # here; try the plausible spellings and confirm with sx_get_panel_type.
    set made ""
    foreach type {eye eyediagram eye_diagram EYE Eye "eye diagram"} {
        if {[catch {$WINDOW_CMD $type} p]} {
            if {[eye::batch_error $p]} { return "" }
            continue
        }
        if {[eye::panel_is_eye $p]} {
            set CUR_WIN $p
            eye::log "eye panel created: $WINDOW_CMD \"$type\" -> $p"
            eye::set_title $p $title
            return $p
        }
        # Wrong type but a panel exists -- keep the first one as a fallback.
        if {$made eq ""} { set made $p }
    }

    # Nothing took a type argument.  Make a bare panel and try to convert it.
    if {$made eq ""} {
        if {[catch {$WINDOW_CMD} made]} {
            if {[eye::batch_error $made]} { return "" }
            eye::log "$WINDOW_CMD failed: $made"
            return ""
        }
    }
    foreach setter {sx_set_panel_y_type sx_set_panel_x_type} {
        if {[llength [info commands $setter]] == 0} { continue }
        foreach type {eye eyediagram eye_diagram EYE} {
            catch {$setter $made $type}
        }
    }

    set CUR_WIN $made
    eye::set_title $made $title
    if {[eye::panel_is_eye $made]} {
        eye::log "eye panel created (type set after creation) -> $made"
    } else {
        eye::log "WARNING: panel $made is type '[eye::panel_type $made]', not an eye"
        eye::log "WARNING: eyes will not render here.  Run probe_eye.tcl in the"
        eye::log "WARNING: GUI and pin sx_new_panel's real type argument."
    }
    return $CUR_WIN
}

proc eye::panel_type {panel} {
    if {[llength [info commands sx_get_panel_type]] == 0} { return "" }
    if {[catch {sx_get_panel_type $panel} t]} { return "" }
    return $t
}

# Unknown type token means unknown reply, so treat "could not ask" as a pass
# and let the WARNING path above depend on an answer we actually got.
proc eye::panel_is_eye {panel} {
    set t [eye::panel_type $panel]
    if {$t eq ""} { return 1 }
    return [string match -nocase "*eye*" $t]
}

proc eye::set_title {panel title} {
    if {[llength [info commands sx_set_panel_title]] == 0} { return }
    catch {sx_set_panel_title $panel $title}
}

# Graphical ACE commands refuse to run under -no_gui.  Recognise that once and
# stop retrying, so a batch run does not emit 18 identical errors.
proc eye::batch_error {msg} {
    variable BATCH
    if {![string match -nocase "*batch mode*" $msg]} { return 0 }
    if {!$BATCH} {
        set BATCH 1
        eye::log "graphical commands unavailable in batch mode -- skipping display."
        eye::log "measurement is unaffected; open the replay script in the GUI to draw."
    }
    return 1
}

# Put a measured eye on screen.  No-op (with a warning once) when the build's
# plot command could not be resolved.
proc eye::show {eye {label ""}} {
    variable PLOT_CMD
    variable PLOT_FORM
    variable CUR_WIN
    variable BATCH
    eye::probe
    if {$PLOT_CMD eq "" || $BATCH} { return 0 }

    # Argument order is not documented outside SolvNet, so try the plausible
    # forms once, remember whichever works, and use only that one afterwards.
    if {$PLOT_FORM ne ""} {
        if {[catch {eval $PLOT_FORM [list $eye $CUR_WIN]} err]} {
            eye::log "$PLOT_CMD failed for $label: $err"
            return 0
        }
        return 1
    }
    foreach form {{eye} {win eye} {eye win}} {
        set args {}
        foreach tok $form {
            lappend args [expr {$tok eq "eye" ? $eye : $CUR_WIN}]
        }
        if {[llength $args] > 1 && $CUR_WIN eq ""} { continue }
        if {![catch {$PLOT_CMD {*}$args} err]} {
            set PLOT_FORM [list apply {{cmd form e w} {
                set a {}
                foreach t $form { lappend a [expr {$t eq "eye" ? $e : $w}] }
                $cmd {*}$a
            }} $PLOT_CMD $form]
            eye::log "display: $PLOT_CMD with args ($form)"
            return 1
        }
        if {[eye::batch_error $err]} { return 0 }
        set last $err
    }
    eye::log "$PLOT_CMD failed for $label (all arg forms): $last"
    return 0
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
