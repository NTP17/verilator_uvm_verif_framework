// AXI4-Lite Agent
class axi4lite_agent extends uvm_agent;

    `uvm_component_utils(axi4lite_agent)

    axi4lite_driver    drv;
    uvm_sequencer #(axi4lite_seq_item) sqr;
    axi4lite_monitor   mon;

    uvm_analysis_port #(axi4lite_seq_item) ap;

    function new(string name = "axi4lite_agent", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        mon = axi4lite_monitor::type_id::create("mon", this);
        if (get_is_active() == UVM_ACTIVE) begin
            drv = axi4lite_driver::type_id::create("drv", this);
            sqr = uvm_sequencer#(axi4lite_seq_item)::type_id::create("sqr", this);
        end
    endfunction

    function void connect_phase(uvm_phase phase);
        super.connect_phase(phase);
        ap = mon.ap;
        if (get_is_active() == UVM_ACTIVE) begin
            drv.seq_item_port.connect(sqr.seq_item_export);
        end
    endfunction

endclass
