#!/usr/bin/env wv
# ===========================================================================
# Launcher, for wrappers that do not forward script arguments to Tcl.
#
#   sx_sub -no_gui run_<config>.tcl
#
# Keep this next to measure_eye.tcl.  If it must live elsewhere, set
# DM_EYE_HOME to the directory holding measure_eye.tcl:
#   setenv DM_EYE_HOME /path/to/DM_AUTOMATION      (csh/tcsh)
#   export DM_EYE_HOME=/path/to/DM_AUTOMATION      (bash/sh)
#
# The config name comes from this file's own name, so adding one is a copy
# with no edit:  cp run_nand_read.tcl run_lp5x_read.tcl
# ===========================================================================

set _self [info script]
set _name ""
if {$_self ne ""} {
    set _self [file normalize $_self]
    set _name [file rootname [file tail $_self]]
}

# Config name: from this file's name, else DM_EYE_CFG must already be set.
if {[string match "run_*" $_name]} {
    set DM_EYE_CFG [string range $_name 4 end]
} elseif {![info exists DM_EYE_CFG] && ![info exists env(DM_EYE_CFG)]} {
    error "launcher cannot tell which config to use.\n\
      Its own name came back as '[expr {$_name eq {} ? {<empty>} : $_name}]',\n\
      which means it is not named run_<config>.tcl, or this wrapper does not\n\
      expose the script path to Tcl.  Set the config explicitly instead:\n\
    \    setenv DM_EYE_CFG nand_read   (csh/tcsh)"
}

# Find measure_eye.tcl: beside this file, then DM_EYE_HOME, then the cwd.
set _tried {}
set _drv ""
foreach _d [list [expr {$_self eq "" ? "" : [file dirname $_self]}] \
                 [expr {[info exists env(DM_EYE_HOME)] ? $env(DM_EYE_HOME) : ""}] \
                 [pwd]] {
    if {$_d eq ""} continue
    set _c [file join $_d measure_eye.tcl]
    lappend _tried $_c
    if {[file readable $_c]} { set _drv $_c; break }
}
if {$_drv eq ""} {
    error "measure_eye.tcl not found. Looked in:\n  [join $_tried \n  ]\n\
      Keep this launcher next to measure_eye.tcl, or set DM_EYE_HOME to the\n\
      directory holding it."
}

source $_drv
