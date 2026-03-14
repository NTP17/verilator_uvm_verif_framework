// AXI4-Lite Interface Definition
interface axi4lite_if #(
    parameter ADDR_WIDTH = 32,
    parameter DATA_WIDTH = 32
) (
    input logic clk,
    input logic rst_n
);

    // Write Address Channel
    logic                    awvalid;
    logic                    awready;
    logic [ADDR_WIDTH-1:0]   awaddr;
    logic [2:0]              awprot;

    // Write Data Channel
    logic                    wvalid;
    logic                    wready;
    logic [DATA_WIDTH-1:0]   wdata;
    logic [DATA_WIDTH/8-1:0] wstrb;

    // Write Response Channel
    logic                    bvalid;
    logic                    bready;
    logic [1:0]              bresp;

    // Read Address Channel
    logic                    arvalid;
    logic                    arready;
    logic [ADDR_WIDTH-1:0]   araddr;
    logic [2:0]              arprot;

    // Read Data Channel
    logic                    rvalid;
    logic                    rready;
    logic [DATA_WIDTH-1:0]   rdata;
    logic [1:0]              rresp;

    // Master clocking block
    clocking mst_cb @(posedge clk);
        default input #1 output #1;
        output awvalid, awaddr, awprot;
        input  awready;
        output wvalid, wdata, wstrb;
        input  wready;
        input  bvalid, bresp;
        output bready;
        output arvalid, araddr, arprot;
        input  arready;
        input  rvalid, rdata, rresp;
        output rready;
    endclocking

    // Monitor clocking block
    clocking mon_cb @(posedge clk);
        default input #1;
        input awvalid, awaddr, awprot, awready;
        input wvalid, wdata, wstrb, wready;
        input bvalid, bresp, bready;
        input arvalid, araddr, arprot, arready;
        input rvalid, rdata, rresp, rready;
    endclocking

    modport master (clocking mst_cb, input clk, rst_n);
    modport monitor (clocking mon_cb, input clk, rst_n);

endinterface
