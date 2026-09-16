#!/usr/bin/env wv
# ===========================================================================
# Launcher, for wrappers that do not forward script arguments to Tcl.
#
#   sx_sub -no_gui run_<config>.tcl
#
# The config name is taken from this file's own name, so adding one is a copy
# with no edit:  cp run_nand_read.tcl run_lp5x_read.tcl
# ===========================================================================

set _self [file normalize [info script]]
set _name [file rootname [file tail $_self]]
if {![string match "run_*" $_name]} {
    error "launcher must be named run_<config>.tcl, not [file tail $_self]"
}
set DM_EYE_CFG [string range $_name 4 end]
source [file join [file dirname $_self] measure_eye.tcl]
