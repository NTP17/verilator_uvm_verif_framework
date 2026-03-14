// Simple AXI4-Lite Slave - 16-word Register File
// Address range: 0x00 - 0x3C (16 x 32-bit registers)
module axi4lite_slave_regfile #(
    parameter ADDR_WIDTH = 32,
    parameter DATA_WIDTH = 32,
    parameter NUM_REGS   = 16
) (
    input  logic                    clk,
    input  logic                    rst_n,

    // Write Address Channel
    input  logic                    awvalid,
    output logic                    awready,
    input  logic [ADDR_WIDTH-1:0]   awaddr,
    input  logic [2:0]              awprot,

    // Write Data Channel
    input  logic                    wvalid,
    output logic                    wready,
    input  logic [DATA_WIDTH-1:0]   wdata,
    input  logic [DATA_WIDTH/8-1:0] wstrb,

    // Write Response Channel
    output logic                    bvalid,
    input  logic                    bready,
    output logic [1:0]              bresp,

    // Read Address Channel
    input  logic                    arvalid,
    output logic                    arready,
    input  logic [ADDR_WIDTH-1:0]   araddr,
    input  logic [2:0]              arprot,

    // Read Data Channel
    output logic                    rvalid,
    input  logic                    rready,
    output logic [DATA_WIDTH-1:0]   rdata,
    output logic [1:0]              rresp
);

    // Register storage
    logic [DATA_WIDTH-1:0] regs [NUM_REGS];

    // Internal state
    logic        aw_done, w_done;
    logic [ADDR_WIDTH-1:0] aw_addr_q;
    logic [DATA_WIDTH-1:0] w_data_q;
    logic [DATA_WIDTH/8-1:0] w_strb_q;

    // Address decode helper
    function automatic int unsigned addr_to_idx(input logic [ADDR_WIDTH-1:0] addr);
        return (addr >> 2) & (NUM_REGS - 1);
    endfunction

    // ---------- Write Address Channel ----------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_done   <= 1'b0;
            aw_addr_q <= '0;
        end else if (awvalid && awready) begin
            aw_done   <= 1'b1;
            aw_addr_q <= awaddr;
        end else if (bvalid && bready) begin
            aw_done <= 1'b0;
        end
    end
    assign awready = !aw_done && !bvalid;

    // ---------- Write Data Channel ----------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            w_done   <= 1'b0;
            w_data_q <= '0;
            w_strb_q <= '0;
        end else if (wvalid && wready) begin
            w_done   <= 1'b1;
            w_data_q <= wdata;
            w_strb_q <= wstrb;
        end else if (bvalid && bready) begin
            w_done <= 1'b0;
        end
    end
    assign wready = !w_done && !bvalid;

    // ---------- Write Response Channel ----------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bvalid <= 1'b0;
            bresp  <= 2'b00;
        end else if (aw_done && w_done && !bvalid) begin
            bvalid <= 1'b1;
            bresp  <= 2'b00; // OKAY
            // Write register with strobe
            for (int i = 0; i < DATA_WIDTH/8; i++) begin
                if (w_strb_q[i])
                    regs[addr_to_idx(aw_addr_q)][i*8 +: 8] <= w_data_q[i*8 +: 8];
            end
        end else if (bvalid && bready) begin
            bvalid <= 1'b0;
        end
    end

    // ---------- Read Address Channel ----------
    logic        ar_pending;
    logic [ADDR_WIDTH-1:0] ar_addr_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ar_pending <= 1'b0;
            ar_addr_q  <= '0;
        end else if (arvalid && arready) begin
            ar_pending <= 1'b1;
            ar_addr_q  <= araddr;
        end else if (rvalid && rready) begin
            ar_pending <= 1'b0;
        end
    end
    assign arready = !ar_pending && !rvalid;

    // ---------- Read Data Channel ----------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rvalid <= 1'b0;
            rdata  <= '0;
            rresp  <= 2'b00;
        end else if (ar_pending && !rvalid) begin
            rvalid <= 1'b1;
            rdata  <= regs[addr_to_idx(ar_addr_q)];
            rresp  <= 2'b00; // OKAY
        end else if (rvalid && rready) begin
            rvalid <= 1'b0;
        end
    end

    // ---------- Register Reset ----------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < NUM_REGS; i++)
                regs[i] <= '0;
        end
    end

endmodule
