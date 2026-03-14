// SVA → UVM error reporting bridge
// Include this file inside the top-level testbench module.
// Requires: import uvm_pkg::*; and import sva_dpi_pkg::*;

export "DPI-C" function sva_uvm_error;
function void sva_uvm_error(string id, string msg);
    uvm_pkg::uvm_report_error(id, msg, UVM_NONE, `uvm_file, `uvm_line);
endfunction
