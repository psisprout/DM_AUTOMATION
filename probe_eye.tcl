#!/usr/bin/env wv
# ===========================================================================
# probe_eye.tcl -- focused signature probe.  Run from the GUI:
#
#   sx_sub           then  Run ACE script  ->  probe_eye.tcl
#
# Unlike probe_ace.tcl this asks about ~20 commands only, and prints the
# result to the console as well as out/eye_usage.txt, so the output is short
# enough to read or paste.
#
# Each command is called with NO arguments inside catch.  ACE answers with its
# usage string, e.g.  sx_add_cursor(panel_obj,<xloc>,<option>.)  -- that error
# IS the signature.  Every line reporting an error is the probe working.
# Run it in the GUI, not under -no_gui, or the graphical ones only answer
# "can't perform graphical command in batch mode".
# ===========================================================================

set WANT {
    sx_new_panel sx_current_panel sx_select_panel sx_pick_panel sx_get_sel_panel
    sx_get_panel_type sx_set_panel_x_type sx_set_panel_y_type
    sx_set_panel_parm sx_set_panel_title sx_get_panel_count sx_get_last_panel
    sx_display_eye sx_display_wave sx_display
    sx_create_eye sx_config_eye sx_measure_eye sx_query_eye
    sx_open_sim_file_read
}

# Plus every session/save/load command this build has -- needed to persist the
# result once the eyes draw correctly.
foreach c [lsort [info commands sx_*]] {
    if {[regexp {session|save|load} $c]} { lappend WANT $c }
}

set OUT_DIR [file join [file dirname [file normalize [info script]]] out]
file mkdir $OUT_DIR
set path [file join $OUT_DIR eye_usage.txt]
set f [open $path w]

proc emit {line} {
    global f
    puts $f $line
    puts $line
}

emit "# focused ACE signature probe"
emit "# tcl: $tcl_patchLevel   generated: [clock format [clock seconds]]"
emit ""

foreach c $WANT {
    if {[llength [info commands $c]] == 0} {
        emit [format "%-26s -- NOT PRESENT" $c]
        continue
    }
    if {[catch {$c} err]} {
        emit [format "%-26s %s" $c $err]
    } else {
        emit [format "%-26s <no error with zero args>" $c]
    }
}

emit ""
emit "# panel type of the currently selected panel, if any:"
foreach probe {sx_get_sel_panel sx_current_panel} {
    if {[llength [info commands $probe]] == 0} { continue }
    if {![catch {$probe} p] && $p ne ""} {
        emit "  $probe -> $p"
        if {[llength [info commands sx_get_panel_type]] > 0} {
            if {![catch {sx_get_panel_type $p} t]} { emit "  sx_get_panel_type -> $t" }
        }
    }
}

close $f
puts ""
puts "written: $path"
