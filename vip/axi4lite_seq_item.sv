// AXI4-Lite Sequence Item
class axi4lite_seq_item extends uvm_sequence_item;

    // Transaction type
    typedef enum bit { READ = 0, WRITE = 1 } txn_type_e;

    rand txn_type_e             txn_type;
    rand bit [31:0]             addr;
    rand bit [31:0]             data;
    rand bit [3:0]              strb;
    rand bit [2:0]              prot;

    // Response fields (set by driver/monitor)
    bit [1:0]                   resp;
    bit [31:0]                  rdata;

    `uvm_object_utils_begin(axi4lite_seq_item)
        `uvm_field_enum(txn_type_e, txn_type, UVM_ALL_ON)
        `uvm_field_int(addr,     UVM_ALL_ON | UVM_HEX)
        `uvm_field_int(data,     UVM_ALL_ON | UVM_HEX)
        `uvm_field_int(strb,     UVM_ALL_ON | UVM_BIN)
        `uvm_field_int(prot,     UVM_ALL_ON | UVM_BIN)
        `uvm_field_int(resp,     UVM_ALL_ON | UVM_HEX)
        `uvm_field_int(rdata,    UVM_ALL_ON | UVM_HEX)
    `uvm_object_utils_end

    function new(string name = "axi4lite_seq_item");
        super.new(name);
    endfunction

    // Constrain address to be word-aligned
    constraint addr_aligned_c {
        addr[1:0] == 2'b00;
    }

    // Strobe must have at least one byte enabled for writes
    constraint default_strb_c {
        txn_type == WRITE -> strb != 4'h0;
    }

    function string convert2string();
        return $sformatf("%s addr=0x%08h data=0x%08h strb=0b%04b resp=%0d rdata=0x%08h",
                         txn_type.name(), addr, data, strb, resp, rdata);
    endfunction

endclass
