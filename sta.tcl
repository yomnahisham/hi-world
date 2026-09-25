read_liberty @LIB@
read_verilog @NET@
link_design @TOP@
if {[llength [get_ports -quiet clk]]} {
  create_clock -name clk -period @P@ [get_ports clk]
  set ins [delete_from_list [all_inputs] [get_ports clk]]
} else {
  create_clock -name clk -period @P@
  set ins [all_inputs]
}
set_input_delay 0 -clock clk $ins
set_output_delay 0 -clock clk [all_outputs]
set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 -pin Y $ins
set_load 0.01 [all_outputs]
report_worst_slack -max -digits 4
report_checks -path_delay max -group_path_count @N@ -fields {cap slew input_pins} -digits 4
