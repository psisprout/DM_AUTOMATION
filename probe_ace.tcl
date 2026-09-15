#!/usr/bin/env wv
# ===========================================================================
# probe_ace.tcl -- dump this WaveView build's ACE command surface.
#
#   wv -no_gui probe_ace.tcl          (or Run ACE from the GUI)
#
# Writes out/ace_commands.txt: every sx_* command, with the display/window
# related ones called out first, plus whatever sx_help / help returns for
# them.  Commit that file (or paste it) so the display calls can be pinned
# instead of guessed.
# ===========================================================================

set OUT_DIR [file join [file dirname [file normalize [info script]]] out]
file mkdir $OUT_DIR
set path [file join $OUT_DIR ace_commands.txt]
set f [open $path w]

set all [lsort [info commands sx_*]]
puts $f "# ACE command dump"
puts $f "# generated : [clock format [clock seconds]]"
puts $f "# tcl       : $tcl_patchLevel"
puts $f "# sx_* count: [llength $all]"
puts $f ""

# The ones most likely to be the missing display step.
puts $f "=========== LIKELY DISPLAY / WINDOW / PLOT COMMANDS ==========="
foreach c $all {
    if {[regexp {plot|display|show|draw|add|window|panel|wave|graph|curve|zoom|open|new|current|frame|tab} $c]} {
        puts $f $c
    }
}

puts $f ""
puts $f "=========== EYE COMMANDS ==========="
foreach c $all { if {[string match "*eye*" $c]} { puts $f $c } }

puts $f ""
puts $f "=========== SESSION / FILE COMMANDS ==========="
foreach c $all { if {[regexp {session|save|load|file|close|source|sub|exec|script} $c]} { puts $f $c } }

puts $f ""
puts $f "=========== ALL sx_* COMMANDS ==========="
foreach c $all { puts $f $c }

# Per-command help, best effort -- ACE builds differ in which of these exist.
puts $f ""
puts $f "=========== HELP TEXT (best effort) ==========="
foreach c $all {
    if {![regexp {eye|plot|display|show|add|window|panel|session|save|curve} $c]} { continue }
    set txt ""
    foreach attempt [list [list sx_help $c] [list help $c] [list $c -help] [list $c -h]] {
        if {[llength [info commands [lindex $attempt 0]]] == 0} { continue }
        if {![catch {eval $attempt} r] && [string trim $r] ne ""} { set txt $r; break }
    }
    if {$txt eq "" && ![catch {info args $c} a]} { set txt "args: $a" }
    if {$txt ne ""} {
        puts $f "--- $c ---"
        puts $f $txt
    }
}

close $f
puts "ACE command dump written: $path"
puts "sx_* commands found: [llength $all]"
