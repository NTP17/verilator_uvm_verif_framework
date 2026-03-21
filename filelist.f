// Project file list
// SV sources are preprocessed into gen/ then compiled by Verilator
// Included files (.sv in +incdir paths) are also preprocessed automatically
// Note: lib/ and gen/ incdirs + sva_dpi_pkg.sv are added automatically by the Makefile

// Include directories
+incdir+vip
+incdir+tb

// Top module
--top-module tb_top

// VIP (interface & SVA module must be separate compilation units)
vip/axi4lite_if.sv
vip/axi4lite_sva.sv
vip/axi4lite_pkg.sv

// RTL
rtl/axi4lite_slave_regfile.sv

// Testbench
tb/tb_top.sv
