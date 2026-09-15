#!/usr/bin/env wv
# ===========================================================================
# probe_ace.tcl -- dump this WaveView build's ACE command surface.
#
#   sx_sub -no_gui probe_ace.tcl          list only, invokes nothing (safe)
#   sx_sub -no_gui probe_ace.tcl -usage   also capture usage strings
#
# Run it from the GUI too (Run ACE script): the graphical commands report
# their real signatures there instead of "batch mode".
#
# -usage calls each candidate with NO arguments inside catch and records the
# resulting error.  ACE reports these as usage strings, e.g.
#     sx_add_cursor(panel_obj,<xloc>,<option>.) ; argument type error
# which is the only signature documentation available without SolvNet.  It is
# noisy by design: every probed command reports an error, and graphical ones
# add "can't perform graphical command in batch mode".  All of it is captured
# to the output file, not acted on.  Do NOT use -usage in a flow that matters.
# ===========================================================================

set WANT_USAGE [expr {[info exists argv] && [lsearch -exact $argv "-usage"] >= 0}]

set OUT_DIR [file join [file dirname [file normalize [info script]]] out]
file mkdir $OUT_DIR
set path [file join $OUT_DIR ace_commands.txt]
set f [open $path w]

set all [lsort [info commands sx_*]]
puts $f "# ACE command dump"
puts $f "# generated  : [clock format [clock seconds]]"
puts $f "# tcl        : $tcl_patchLevel"
puts $f "# sx_* count : [llength $all]"
puts $f "# usage probe: [expr {$WANT_USAGE ? {on} : {off}}]"
puts $f ""

proc section {title pattern} {
    global f all
    puts $f "=========== $title ==========="
    foreach c $all { if {[regexp $pattern $c]} { puts $f $c } }
    puts $f ""
}

section "EYE COMMANDS"                 {eye}
section "DISPLAY / WINDOW / PANEL"     {plot|display|show|draw|window|panel|graph|curve|frame|tab}
section "CURSOR / LABEL / ANNOTATION"  {cursor|label|marker|text|annot|title|legend}
section "SESSION / FILE / SCRIPT"      {session|save|load|file|close|source|sub|exec|script|import|export}
section "ALL sx_* COMMANDS"            {.}

# --------------------------------------------------------------------------
# Usage capture.  Opt-in: this INVOKES commands.
# --------------------------------------------------------------------------
if {$WANT_USAGE} {
    puts $f "=========== USAGE STRINGS (from no-arg error text) ==========="
    puts $f "# Each line below is the error ACE returned when the command was"
    puts $f "# called with no arguments.  For most commands that error IS the"
    puts $f "# signature.  Nothing here was executed for effect."
    puts $f ""
    foreach c $all {
        if {![regexp {eye|plot|display|show|window|panel|cursor|label|curve|save|session} $c]} { continue }
        if {[catch {$c} err]} {
            puts $f [format "%-28s %s" $c $err]
        } else {
            puts $f [format "%-28s <no error with zero args>" $c]
        }
    }
    puts $f ""
}

close $f
puts "ACE command dump written: $path"
puts "sx_* commands found: [llength $all]"
if {!$WANT_USAGE} {
    puts "Re-run with -usage to also capture signature strings (noisy: every probed command errors)."
}
