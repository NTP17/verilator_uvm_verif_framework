// AXI4-Lite Testbench Top
module tb_top;

    import uvm_pkg::*;
    `include "uvm_macros.svh"
    import axi4lite_pkg::*;

    logic clk;
    logic rst_n;

    // Clock generation: 10ns period
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    // Reset generation
    initial begin
        rst_n = 0;
        #50;
        rst_n = 1;
    end

    // AXI4-Lite interface instance
    axi4lite_if axi_if (.clk(clk), .rst_n(rst_n));

    // DUT
    axi4lite_slave_regfile #(
        .ADDR_WIDTH (32),
        .DATA_WIDTH (32),
        .NUM_REGS   (16)
    ) dut (
        .clk     (clk),
        .rst_n   (rst_n),
        .awvalid (axi_if.awvalid),
        .awready (axi_if.awready),
        .awaddr  (axi_if.awaddr),
        .awprot  (axi_if.awprot),
        .wvalid  (axi_if.wvalid),
        .wready  (axi_if.wready),
        .wdata   (axi_if.wdata),
        .wstrb   (axi_if.wstrb),
        .bvalid  (axi_if.bvalid),
        .bready  (axi_if.bready),
        .bresp   (axi_if.bresp),
        .arvalid (axi_if.arvalid),
        .arready (axi_if.arready),
        .araddr  (axi_if.araddr),
        .arprot  (axi_if.arprot),
        .rvalid  (axi_if.rvalid),
        .rready  (axi_if.rready),
        .rdata   (axi_if.rdata),
        .rresp   (axi_if.rresp)
    );

    // SVA concurrent assertions (preprocessed by svpp)
    axi4lite_sva sva_i (
        .clk     (clk),
        .rst_n   (rst_n),
        .awvalid (axi_if.awvalid),
        .awready (axi_if.awready),
        .awaddr  (axi_if.awaddr),
        .wvalid  (axi_if.wvalid),
        .wready  (axi_if.wready),
        .bvalid  (axi_if.bvalid),
        .bready  (axi_if.bready),
        .bresp   (axi_if.bresp),
        .arvalid (axi_if.arvalid),
        .arready (axi_if.arready),
        .araddr  (axi_if.araddr),
        .rvalid  (axi_if.rvalid),
        .rready  (axi_if.rready),
        .rresp   (axi_if.rresp)
    );

    // Pass virtual interface to UVM config_db
    initial begin
        uvm_config_db#(virtual axi4lite_if)::set(null, "*", "vif", axi_if);
    end

    // Run UVM test
    initial begin
        run_test();
    end

    // Simulation timeout
    initial begin
        #100_000;
        `uvm_fatal("TIMEOUT", "Simulation timed out")
    end

    // Optional: Waveform dump
    initial begin
        $dumpfile("dump.vcd");
        $dumpvars(0, tb_top);
    end

endmodule
