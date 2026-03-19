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
                    do @(vif.mon_cb);
                    while (!(vif.mon_cb.awvalid && vif.mon_cb.awready));
                    aw_addr = vif.mon_cb.awaddr;
                    aw_prot = vif.mon_cb.awprot;
                end
                begin : mon_w
                    do @(vif.mon_cb);
                    while (!(vif.mon_cb.wvalid && vif.mon_cb.wready));
                    w_data = vif.mon_cb.wdata;
                    w_strb = vif.mon_cb.wstrb;
                end
            join

            // Capture B handshake
            do @(vif.mon_cb);
            while (!(vif.mon_cb.bvalid && vif.mon_cb.bready));
            b_resp = vif.mon_cb.bresp;

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
            do @(vif.mon_cb);
            while (!(vif.mon_cb.arvalid && vif.mon_cb.arready));

            item = axi4lite_seq_item::type_id::create("mon_rd_item");
            item.txn_type = axi4lite_seq_item::READ;
            item.addr     = vif.mon_cb.araddr;
            item.prot     = vif.mon_cb.arprot;

            // Capture R handshake
            do @(vif.mon_cb);
            while (!(vif.mon_cb.rvalid && vif.mon_cb.rready));
            item.rdata = vif.mon_cb.rdata;
            item.resp  = vif.mon_cb.rresp;

            `uvm_info(get_type_name(), $sformatf("MON READ:  %s", item.convert2string()), UVM_HIGH)
            ap.write(item);
        end
    endtask

endclass
