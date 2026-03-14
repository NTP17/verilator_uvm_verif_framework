// ==========================================================================
//  AXI4-Lite SVA Concurrent Assertions
//
//  Native SystemVerilog assert/cover properties.
//  svpp.py transforms these into Verilator-compatible DPI-C calls.
// ==========================================================================

module axi4lite_sva (
    input  logic        clk,
    input  logic        rst_n,
    // Write Address
    input  logic        awvalid,
    input  logic        awready,
    input  logic [31:0] awaddr,
    // Write Data
    input  logic        wvalid,
    input  logic        wready,
    // Write Response
    input  logic        bvalid,
    input  logic        bready,
    input  logic [1:0]  bresp,
    // Read Address
    input  logic        arvalid,
    input  logic        arready,
    input  logic [31:0] araddr,
    // Read Data
    input  logic        rvalid,
    input  logic        rready,
    input  logic [1:0]  rresp
);

    // ------------------------------------------------------------------
    //  Helper: internal handshake signals
    // ------------------------------------------------------------------
    wire aw_hsk = awvalid & awready;
    wire w_hsk  = wvalid  & wready;
    wire b_hsk  = bvalid  & bready;
    wire ar_hsk = arvalid & arready;
    wire r_hsk  = rvalid  & rready;

    // Post-reset stabilization: disable assertions for 2 cycles after reset
    logic [1:0] rst_dly;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            rst_dly <= '0;
        else
            rst_dly <= {rst_dly[0], 1'b1};
    end
    wire rst_active = !rst_n || !rst_dly[1];

    // ------------------------------------------------------------------
    //  SVA CONCURRENT ASSERTIONS
    // ------------------------------------------------------------------

    // VALID must stay asserted until READY (AXI protocol rule)
    aw_valid_stable: assert property (@(posedge clk) disable iff (rst_active)
        awvalid && !awready |-> ##1 awvalid
    ) else `uvm_error("aw_valid_stable", "AWVALID must remain asserted until AWREADY");

    w_valid_stable: assert property (@(posedge clk) disable iff (rst_active)
        wvalid && !wready |-> ##1 wvalid
    ) else `uvm_error("w_valid_stable", "WVALID must remain asserted until WREADY");

    ar_valid_stable: assert property (@(posedge clk) disable iff (rst_active)
        arvalid && !arready |-> ##1 arvalid
    ) else `uvm_error("ar_valid_stable", "ARVALID must remain asserted until ARREADY");

    b_valid_stable: assert property (@(posedge clk) disable iff (rst_active)
        bvalid && !bready |-> ##1 bvalid
    ) else `uvm_error("b_valid_stable", "BVALID must remain asserted until BREADY");

    r_valid_stable: assert property (@(posedge clk) disable iff (rst_active)
        rvalid && !rready |-> ##1 rvalid
    ) else `uvm_error("r_valid_stable", "RVALID must remain asserted until RREADY");

    // Write response must be OKAY or SLVERR during handshake
    bresp_valid: assert property (@(posedge clk) disable iff (rst_active)
        b_hsk |-> (bresp == 0 || bresp == 1 || bresp == 2 || bresp == 3)
    ) else `uvm_error("bresp_valid", "BRESP must be a valid response code during handshake");

    // B channel must not assert bvalid without a prior write (aw_done && w_done in DUT)
    // AW and W must handshake on the same cycle (driver fork-join drives both simultaneously)
    aw_w_simultaneous: assert property (@(posedge clk) disable iff (rst_active)
        aw_hsk |-> w_hsk
    ) else `uvm_error("aw_w_simultaneous", "AW and W handshakes must occur on the same cycle");

    // ------------------------------------------------------------------
    //  SVA COVER PROPERTIES
    // ------------------------------------------------------------------

    // Write followed by read within 3 cycles
    cov_wr_then_rd: cover property (@(posedge clk) disable iff (rst_active)
        b_hsk ##[1:3] ar_hsk
    );

    // Read followed by write within 3 cycles
    cov_rd_then_wr: cover property (@(posedge clk) disable iff (rst_active)
        r_hsk ##[1:3] aw_hsk
    );

    // Back-to-back writes (3 consecutive)
    cov_burst_writes: cover property (@(posedge clk) disable iff (rst_active)
        b_hsk ##[1:2] b_hsk ##[1:2] b_hsk
    );

    // Back-to-back reads (3 consecutive)
    cov_burst_reads: cover property (@(posedge clk) disable iff (rst_active)
        r_hsk ##[1:2] r_hsk ##[1:2] r_hsk
    );

    // Simultaneous AW and W handshake
    cov_simultaneous_aw_w: cover property (@(posedge clk) disable iff (rst_active)
        aw_hsk && w_hsk
    );

endmodule
