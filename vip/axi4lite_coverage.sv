// AXI4-Lite Functional Coverage Subscriber
// Uses native SystemVerilog covergroup syntax.
// svpp.py transforms covergroups into Verilator-compatible DPI-C calls.
class axi4lite_coverage extends uvm_subscriber #(axi4lite_seq_item);

    `uvm_component_utils(axi4lite_coverage)

    // Sampled transaction fields
    bit [31:0] txn_addr;
    bit [3:0]  txn_strb;
    bit [1:0]  txn_resp;
    bit        txn_is_write;

    covergroup axi4lite_txn_cg;

        wr_addr_cp: coverpoint txn_addr[5:2] {
            bins low_regs  = {[0:3]};
            bins mid_regs  = {[4:11]};
            bins high_regs = {[12:15]};
        }

        rd_addr_cp: coverpoint txn_addr[5:2] {
            bins low_regs  = {[0:3]};
            bins mid_regs  = {[4:11]};
            bins high_regs = {[12:15]};
        }

        wstrb_cp: coverpoint txn_strb {
            bins byte0     = {4'b0001};
            bins byte1     = {4'b0010};
            bins byte2     = {4'b0100};
            bins byte3     = {4'b1000};
            bins half_lo   = {4'b0011};
            bins half_hi   = {4'b1100};
            bins full      = {4'b1111};
            bins others    = default;
            illegal_bins zero_strb = {4'b0000};
        }

        bresp_cp: coverpoint txn_resp {
            bins okay   = {0};
            bins exokay = {1};
            bins slverr = {2};
            bins decerr = {3};
        }

        txn_type_cp: coverpoint txn_is_write {
            bins write_txn = {1};
            bins read_txn  = {0};
        }

        addr_x_strb: cross wr_addr_cp, wstrb_cp;

    endgroup

    function new(string name = "axi4lite_coverage", uvm_component parent = null);
        super.new(name, parent);
        axi4lite_txn_cg = new();
    endfunction

    function void write(axi4lite_seq_item t);
        txn_addr     = t.addr;
        txn_strb     = t.strb;
        txn_resp     = t.resp;
        txn_is_write = (t.txn_type == axi4lite_seq_item::WRITE);
        axi4lite_txn_cg.sample();
    endfunction

endclass
