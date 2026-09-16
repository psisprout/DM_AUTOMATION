#!/usr/bin/env wv
# ===========================================================================
# run_example.tcl -- launcher, for when sx_sub does not forward script
#                    arguments to the Tcl interpreter.
#
#   sx_sub -no_gui run_example.tcl
#
# Copy this per protocol (run_nand_read.tcl, run_lp5x_ca.tcl, ...) and change
# the one line.  The name is a config under cfg/, with or without .tcl; a full
# path works too.
# ===========================================================================

set DM_EYE_CFG nand_read

source [file join [file dirname [file normalize [info script]]] measure_eye.tcl]
