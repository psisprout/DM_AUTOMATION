# ---------------------------------------------------------------------------
# session.tcl -- write WaveView .sx session files directly.
#
# The measurement pass already knows every signal name and the per-byte vref,
# and a session is a text file, so it can just be written -- no GUI, no ACE
# display commands.
#
# The panel block is NOT hard coded.  A real saved .sx is read as the
# reference and its panel_begin / line / panel_end lines are reused verbatim,
# with only the fields that must vary substituted.  Every other token --
# including any this code does not model -- is preserved byte for byte, so a
# new protocol means a new cfg/ file plus its own reference .sx, not a code
# change.
# ---------------------------------------------------------------------------

namespace eval sess {
    variable WARNED [dict create]
}

# --------------------------------------------------------------------------
# Units.  The format is not uniform: times are written in scientific notation
# (eye_shift=-1.5625e-10 for -156.25p) while voltages keep an SI suffix
# (em_vac=25m).  Config values use SPICE style, so convert per field.
# --------------------------------------------------------------------------
proc sess::to_num {s} {
    set s [string trim $s]
    if {[string is double -strict $s]} { return [expr {double($s)}] }
    if {![regexp -nocase {^([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)([a-z]+)$} $s -> num suf]} {
        return ""
    }
    switch -- [string tolower $suf] {
        f       { set m 1e-15 }
        p       { set m 1e-12 }
        n       { set m 1e-9 }
        u       { set m 1e-6 }
        m       { set m 1e-3 }
        k       { set m 1e3 }
        meg     { set m 1e6 }
        g       { set m 1e9 }
        t       { set m 1e12 }
        default { return "" }
    }
    return [expr {double($num) * $m}]
}

proc sess::fmt_sci {v} {
    set n [sess::to_num $v]
    if {$n eq ""} { return $v }
    return [format "%g" $n]
}

proc sess::fmt_milli {v} {
    set n [sess::to_num $v]
    if {$n eq ""} { return $v }
    # Always the m suffix: a sweep step finer than 1 mV must not silently
    # switch the field to a bare volt value.
    return "[format {%g} [expr {$n * 1000.0}]]m"
}

proc sess::fmt {cfg key v} {
    set how raw
    if {[dict exists $cfg session_fmt $key]} { set how [dict get $cfg session_fmt $key] }
    switch -- $how {
        sci   { return [sess::fmt_sci $v] }
        milli { return [sess::fmt_milli $v] }
        default { return $v }
    }
}

# --------------------------------------------------------------------------
# Trace colour.  attr= cycles per bit within a byte as <i>:<i mod N>:1:0,
# restarting at each byte, so byte 0 and byte 1 share the same palette:
#   0:0:1:0  1:1:1:0 ... 7:7:1:0  8:0:1:0
# N is attr_colors (8).  The trailing 1:0 is carried from the reference.
# --------------------------------------------------------------------------
proc sess::fmt_attr {cfg i ref_val} {
    set n 8
    if {[dict exists $cfg attr_colors]} { set n [dict get $cfg attr_colors] }
    set tail "1:0"
    set parts [split $ref_val :]
    if {[llength $parts] >= 4} { set tail [join [lrange $parts 2 end] :] }
    return "$i:[expr {$i % $n}]:$tail"
}

proc sess::get_kv {line key} {
    if {[regexp -- "(?:^|\[ 	\])$key=(\[^ 	\]*)" $line -> v]} { return $v }
    return ""
}

# --------------------------------------------------------------------------
# Split a reference .sx into header / eye panel / empty panel / footer.
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
            header { lappend hdr $ln }
            footer { lappend ftr $ln }
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
# --------------------------------------------------------------------------
proc sess::kv {line key val} {
    set k [string map {. \\. - \\- + \\+} $key]
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
        eye::log "WARNING: '$key=' not found in the reference .sx panel."
        eye::log "WARNING: $ctx will keep the reference value instead."
    }
    return $ok
}

# --------------------------------------------------------------------------
# One panel block for one bit of one file.
# --------------------------------------------------------------------------
proc sess::panel_block {ref pidx cols fidx data_sig trig_sig vref cfg bit_idx} {
    set out {}
    foreach ln [dict get $ref panel] {
        set t [string trim $ln]
        if {[string match "panel_begin*" $t]} {
            sess::kv! ln pidx      $pidx
            sess::kv! ln ridx      [expr {$pidx / $cols}]
            sess::kv! ln cidx      [expr {$pidx % $cols}]
            sess::kv! ln eye_ext   "$fidx|$trig_sig"                     "the trigger signal"
            sess::kv! ln em_vref   [sess::fmt $cfg em_vref   $vref]      "vref"
            sess::kv! ln eye_width [sess::fmt $cfg eye_width [dict get $cfg ui]]        "UI"
            sess::kv! ln eye_shift [sess::fmt $cfg eye_shift [dict get $cfg eye_shift]] "the eye shift"
            sess::kv! ln em_vac    [sess::fmt $cfg em_vac    [dict get $cfg vac]]       "vac"
            sess::kv! ln eye_meas  [dict get $cfg eye_type]  "the mask type"
        } elseif {[regexp {^line\s} $t]} {
            sess::kv! ln fidx $fidx
            sess::kv! ln name $data_sig "the data signal"
            sess::kv! ln attr [sess::fmt_attr $cfg $bit_idx [sess::get_kv $ln attr]] \
                "the trace colour"
        }
        lappend out $ln
    }
    return $out
}

# --------------------------------------------------------------------------
# Write one .sx.
#   entries : list of {fsdb results}, one per wdf index.  A single entry gives
#             the usual one-session-per-fsdb file; several give one session
#             holding every file, panels tagged with the matching fidx.
# --------------------------------------------------------------------------
proc sess::write {path cfg entries} {
    set ref  [sess::parse_reference [dict get $cfg session_template]]
    set cols [dict get $cfg grid_cols]

    set out {}

    # Header: replace the reference's wdf line(s) with one per input file.
    set wdf_done 0
    foreach ln [dict get $ref header] {
        if {[regexp {^wdf\s} [string trim $ln]]} {
            if {$wdf_done} { continue }
            set wdf_done 1
            set i 0
            foreach e $entries {
                lappend out "wdf $i \"[file normalize [lindex $e 0]]\" load=used"
                incr i
            }
            continue
        }
        lappend out $ln
    }
    if {!$wdf_done} { error "reference .sx has no wdf line" }

    # Panels, in CSV column order, per file.
    set pidx 0
    set fidx 0
    foreach e $entries {
        lassign $e fsdb results
        foreach b [dict get $cfg byte_order] {
            set trig [eye::strobe_sig $cfg $b]
            set vref [dict get $results $b vref]
            set bit_idx 0
            foreach bit [dict get $cfg bytes $b bits] {
                foreach ln [sess::panel_block $ref $pidx $cols $fidx \
                                [eye::data_sig $cfg $b $bit] $trig $vref $cfg $bit_idx] {
                    lappend out $ln
                }
                incr pidx
                incr bit_idx
            }
        }
        incr fidx
    }
    set eye_panels $pidx

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
    eye::log "session written: $path ($eye_panels eyes, $pidx panels, $fidx wdf)"
}
