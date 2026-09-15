# ---------------------------------------------------------------------------
# session.tcl -- write WaveView session files directly.
#
# No GUI, no ACE display commands: the measurement pass already knows every
# signal name and the per-byte vref, and a session is a text file, so it can
# just be written.
#
# The panel block is NOT hard coded.  A real saved session is read as the
# reference and its panel_begin / line / panel_end lines are reused verbatim,
# with only the fields that must vary substituted.  Every other token --
# including any this code does not understand -- is preserved byte for byte.
# ---------------------------------------------------------------------------

namespace eval sess {
    variable WARNED [dict create]
}

# --------------------------------------------------------------------------
# Split a reference session into header / eye panel / empty panel / footer.
# --------------------------------------------------------------------------
proc sess::parse_reference {path} {
    set f [open $path r]
    set lines [split [string trimright [read $f] \n] \n]
    close $f

    set hdr {}; set ftr {}; set panel {}; set empty {}
    set state header; set cur {}; set has_line 0

    foreach ln $lines {
        set t [string trim $ln]
        if {$state ne "panel" && [string match "panel_begin*" $t]} {
            set state panel; set cur [list $ln]; set has_line 0
            continue
        }
        switch -- $state {
            header  { lappend hdr $ln }
            footer  { lappend ftr $ln }
            panel {
                lappend cur $ln
                if {[regexp {^line\s} $t]} { set has_line 1 }
                if {[string match "panel_end*" $t]} {
                    if {$has_line} {
                        if {![llength $panel]} { set panel $cur }
                    } elseif {![llength $empty]} {
                        set empty $cur
                    }
                    set state footer
                }
            }
        }
    }

    if {![llength $panel]} {
        error "no eye panel (panel_begin ... line ... panel_end) found in $path"
    }
    return [dict create header $hdr panel $panel empty $empty footer $ftr]
}

# --------------------------------------------------------------------------
# Replace key=value in a session line, quoted ("name=...") or bare.
# Returns {line replaced?} so a key the reference does not carry can be
# reported instead of silently doing nothing.
# --------------------------------------------------------------------------
proc sess::kv {line key val} {
    set k [string map {. \\. - \\-} $key]
    if {[regsub -- "\"$k=\[^\"\]*\"" $line "\"$key=$val\"" out]} { return [list $out 1] }
    if {[regsub -- "(^|\[ \t\])$k=\[^ \t\]*" $line "\\1$key=$val" out]} { return [list $out 1] }
    return [list $line 0]
}

proc sess::kv! {linevar key val {ctx ""}} {
    upvar 1 $linevar line
    variable WARNED
    lassign [sess::kv $line $key $val] line ok
    if {!$ok && ![dict exists $WARNED $key]} {
        dict set WARNED $key 1
        eye::log "WARNING: '$key=' not found in the reference session panel."
        eye::log "WARNING: $ctx will keep the reference value instead."
    }
    return $ok
}

proc sess::fmt_vref {v} {
    set mv [expr {$v * 1000.0}]
    if {abs($mv - round($mv)) < 1e-6} { return "[expr {int(round($mv))}]m" }
    return [format "%.6g" $v]
}

# --------------------------------------------------------------------------
# Build one panel block (list of lines) for a single bit.
# --------------------------------------------------------------------------
proc sess::panel_block {tpl pidx cols data_sig trig_sig vref cfg} {
    set ridx [expr {$pidx / $cols}]
    set cidx [expr {$pidx % $cols}]
    set out {}
    foreach ln $tpl {
        set t [string trim $ln]
        if {[string match "panel_begin*" $t]} {
            sess::kv! ln pidx     $pidx
            sess::kv! ln ridx     $ridx
            sess::kv! ln cidx     $cidx
            sess::kv! ln eye_ext  "0|$trig_sig"      "the trigger signal"
            sess::kv! ln em_vref  [sess::fmt_vref $vref] "vref"
            sess::kv! ln eye_width  [dict get $cfg ui]        "UI"
            sess::kv! ln eye_shift  [dict get $cfg eye_shift]  "the eye shift"
            sess::kv! ln em_vac     [dict get $cfg vac]        "vac"
        } elseif {[regexp {^line\s} $t]} {
            sess::kv! ln name $data_sig "the data signal"
        }
        lappend out $ln
    }
    return $out
}

# --------------------------------------------------------------------------
# Write one session for one FSDB.
#   results : dict  byte -> {vref <v> per_bit {bit ps ...}}
# --------------------------------------------------------------------------
proc sess::write {path cfg fsdb results} {
    set ref  [sess::parse_reference [dict get $cfg session_template]]
    set cols [dict get $cfg grid_cols]
    set abs  [file normalize $fsdb]

    set out {}

    # Header, with every wdf line collapsed to this one FSDB at index 0.
    set wdf_done 0
    foreach ln [dict get $ref header] {
        if {[regexp {^wdf\s} [string trim $ln]]} {
            if {$wdf_done} { continue }
            set wdf_done 1
            lassign [sess::kv $ln load used] _ _
            lappend out "wdf 0 \"$abs\" load=used"
            continue
        }
        lappend out $ln
    }
    if {!$wdf_done} { error "reference session has no wdf line" }

    # One panel per bit, in CSV column order.
    set pidx 0
    foreach b [dict get $cfg byte_order] {
        set bc     [dict get $cfg bytes $b]
        set prefix [dict get $bc pad_prefix]
        set trig   [eye::strobe_signal_name $prefix \
                        [dict get $cfg pdqs] [dict get $cfg ndqs] [dict get $bc dqs_idx]]
        set vref   [dict get $results $b vref]
        foreach bit [dict get $bc bits] {
            foreach ln [sess::panel_block [dict get $ref panel] $pidx $cols \
                            [eye::data_signal_name $prefix $bit] $trig $vref $cfg] {
                lappend out $ln
            }
            incr pidx
        }
    }

    # Pad the last grid row with empty panels, as the reference does.
    if {[llength [dict get $ref empty]] > 0} {
        while {$pidx % $cols != 0} {
            foreach ln [dict get $ref empty] {
                set t [string trim $ln]
                if {[string match "panel_begin*" $t]} {
                    sess::kv! ln pidx $pidx
                    sess::kv! ln ridx [expr {$pidx / $cols}]
                    sess::kv! ln cidx [expr {$pidx % $cols}]
                }
                lappend out $ln
            }
            incr pidx
        }
    }

    foreach ln [dict get $ref footer] { lappend out $ln }

    set f [open $path w]
    puts $f [join $out \n]
    close $f
    eye::log "session written: $path ($pidx panels)"
}
