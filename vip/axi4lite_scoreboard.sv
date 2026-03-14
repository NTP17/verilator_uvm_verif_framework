// AXI4-Lite Scoreboard
// Tracks writes and checks reads against expected register values
class axi4lite_scoreboard extends uvm_scoreboard;

    `uvm_component_utils(axi4lite_scoreboard)

    uvm_analysis_imp #(axi4lite_seq_item, axi4lite_scoreboard) analysis_export;

    // Expected memory model
    bit [31:0] mem [bit [31:0]];

    int write_count;
    int read_count;
    int error_count;

    function new(string name = "axi4lite_scoreboard", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        analysis_export = new("analysis_export", this);
    endfunction

    function void write(axi4lite_seq_item item);
        if (item.txn_type == axi4lite_seq_item::WRITE) begin
            mem[item.addr] = apply_strobe(mem.exists(item.addr) ? mem[item.addr] : '0,
                                          item.data, item.strb);
            write_count++;
            `uvm_info(get_type_name(),
                $sformatf("SCB WRITE: addr=0x%08h data=0x%08h (stored=0x%08h)",
                          item.addr, item.data, mem[item.addr]), UVM_HIGH)
        end else begin
            read_count++;
            if (mem.exists(item.addr)) begin
                if (item.rdata !== mem[item.addr]) begin
                    error_count++;
                    `uvm_error(get_type_name(),
                        $sformatf("MISMATCH: addr=0x%08h exp=0x%08h got=0x%08h",
                                  item.addr, mem[item.addr], item.rdata))
                end else begin
                    `uvm_info(get_type_name(),
                        $sformatf("SCB READ OK: addr=0x%08h data=0x%08h",
                                  item.addr, item.rdata), UVM_HIGH)
                end
            end else begin
                // Address never written — expect 0
                if (item.rdata !== 32'h0) begin
                    error_count++;
                    `uvm_error(get_type_name(),
                        $sformatf("MISMATCH (unwritten): addr=0x%08h exp=0x00000000 got=0x%08h",
                                  item.addr, item.rdata))
                end
            end
        end
    endfunction

    function bit [31:0] apply_strobe(bit [31:0] old_data, bit [31:0] new_data, bit [3:0] strb);
        bit [31:0] result;
        for (int i = 0; i < 4; i++) begin
            if (strb[i])
                result[i*8 +: 8] = new_data[i*8 +: 8];
            else
                result[i*8 +: 8] = old_data[i*8 +: 8];
        end
        return result;
    endfunction

    function void report_phase(uvm_phase phase);
        super.report_phase(phase);
        `uvm_info(get_type_name(),
            $sformatf("\n========== SCOREBOARD SUMMARY ==========\n  Writes: %0d\n  Reads:  %0d\n  Errors: %0d\n==========================================",
                      write_count, read_count, error_count), UVM_LOW)
        if (error_count > 0)
            `uvm_error(get_type_name(), $sformatf("TEST FAILED with %0d errors", error_count))
        // else
        //     `uvm_info(get_type_name(), "TEST PASSED - all reads matched expected values", UVM_LOW)
    endfunction

endclass
