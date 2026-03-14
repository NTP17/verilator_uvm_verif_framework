// AXI4-Lite Monitor
class axi4lite_monitor extends uvm_monitor;

    `uvm_component_utils(axi4lite_monitor)

    virtual axi4lite_if vif;

    uvm_analysis_port #(axi4lite_seq_item) ap;

    function new(string name = "axi4lite_monitor", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        ap = new("ap", this);
        if (!uvm_config_db#(virtual axi4lite_if)::get(this, "", "vif", vif))
            `uvm_fatal("NOVIF", "Virtual interface not found")
    endfunction

    task run_phase(uvm_phase phase);
        @(posedge vif.rst_n);
        fork
            monitor_writes();
            monitor_reads();
        join
    endtask

    task monitor_writes();
        axi4lite_seq_item item;
        bit [31:0] aw_addr;
        bit [2:0]  aw_prot;
        bit [31:0] w_data;
        bit [3:0]  w_strb;
        bit [1:0]  b_resp;

        forever begin
            // Capture AW and W handshakes
            fork
                begin : mon_aw
                    do @(posedge vif.clk);
                    while (!(vif.awvalid && vif.awready));
                    aw_addr = vif.awaddr;
                    aw_prot = vif.awprot;
                end
                begin : mon_w
                    do @(posedge vif.clk);
                    while (!(vif.wvalid && vif.wready));
                    w_data = vif.wdata;
                    w_strb = vif.wstrb;
                end
            join

            // Capture B handshake
            do @(posedge vif.clk);
            while (!(vif.bvalid && vif.bready));
            b_resp = vif.bresp;

            item = axi4lite_seq_item::type_id::create("mon_wr_item");
            item.txn_type = axi4lite_seq_item::WRITE;
            item.addr     = aw_addr;
            item.prot     = aw_prot;
            item.data     = w_data;
            item.strb     = w_strb;
            item.resp     = b_resp;

            `uvm_info(get_type_name(), $sformatf("MON WRITE: %s", item.convert2string()), UVM_HIGH)
            ap.write(item);
        end
    endtask

    task monitor_reads();
        axi4lite_seq_item item;

        forever begin
            // Capture AR handshake
            do @(posedge vif.clk);
            while (!(vif.arvalid && vif.arready));

            item = axi4lite_seq_item::type_id::create("mon_rd_item");
            item.txn_type = axi4lite_seq_item::READ;
            item.addr     = vif.araddr;
            item.prot     = vif.arprot;

            // Capture R handshake
            do @(posedge vif.clk);
            while (!(vif.rvalid && vif.rready));
            item.rdata = vif.rdata;
            item.resp  = vif.rresp;

            `uvm_info(get_type_name(), $sformatf("MON READ:  %s", item.convert2string()), UVM_HIGH)
            ap.write(item);
        end
    endtask

endclass
