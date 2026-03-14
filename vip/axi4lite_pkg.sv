// AXI4-Lite VIP Package
package axi4lite_pkg;

    import uvm_pkg::*;
    import sva_dpi_pkg::*;

    `include "axi4lite_seq_item.sv"
    `include "axi4lite_driver.sv"
    `include "axi4lite_monitor.sv"
    `include "axi4lite_coverage.pp.sv"
    `include "axi4lite_agent.sv"
    `include "axi4lite_scoreboard.sv"
    `include "axi4lite_env.sv"
    `include "axi4lite_sequences.sv"

endpackage
