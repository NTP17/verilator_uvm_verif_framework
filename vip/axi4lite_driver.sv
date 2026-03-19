// AXI4-Lite Master Driver
class axi4lite_driver extends uvm_driver #(axi4lite_seq_item);

    `uvm_component_utils(axi4lite_driver)

    virtual axi4lite_if vif;

    function new(string name = "axi4lite_driver", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db#(virtual axi4lite_if)::get(this, "", "vif", vif))
            `uvm_fatal("NOVIF", "Virtual interface not found")
    endfunction

    task run_phase(uvm_phase phase);
        axi4lite_seq_item item;

        // Initialize all master outputs to idle
        reset_signals();

        // Wait for reset de-assertion
        @(posedge vif.rst_n);
        @(vif.mst_cb);

        forever begin
            seq_item_port.get_next_item(item);
            drive_item(item);
            seq_item_port.item_done();
        end
    endtask

    task reset_signals();
        @(vif.mst_cb);
        vif.mst_cb.awvalid <= 1'b0;
        vif.mst_cb.awaddr  <= '0;
        vif.mst_cb.awprot  <= '0;
        vif.mst_cb.wvalid  <= 1'b0;
        vif.mst_cb.wdata   <= '0;
        vif.mst_cb.wstrb   <= '0;
        vif.mst_cb.bready  <= 1'b0;
        vif.mst_cb.arvalid <= 1'b0;
        vif.mst_cb.araddr  <= '0;
        vif.mst_cb.arprot  <= '0;
        vif.mst_cb.rready  <= 1'b0;
    endtask

    task drive_item(axi4lite_seq_item item);
        if (item.txn_type == axi4lite_seq_item::WRITE) begin
            drive_write(item);
        end else begin
            drive_read(item);
        end
    endtask

    task drive_write(axi4lite_seq_item item);
        // Drive AW and W channels simultaneously
        fork
            begin : aw_channel
                @(vif.mst_cb);
                vif.mst_cb.awvalid <= 1'b1;
                vif.mst_cb.awaddr  <= item.addr;
                vif.mst_cb.awprot  <= item.prot;
                do @(vif.mst_cb);
                while (!vif.mst_cb.awready);
                vif.mst_cb.awvalid <= 1'b0;
            end
            begin : w_channel
                @(vif.mst_cb);
                vif.mst_cb.wvalid <= 1'b1;
                vif.mst_cb.wdata  <= item.data;
                vif.mst_cb.wstrb  <= item.strb;
                do @(vif.mst_cb);
                while (!vif.mst_cb.wready);
                vif.mst_cb.wvalid <= 1'b0;
            end
        join

        // Wait for B channel response
        vif.mst_cb.bready <= 1'b1;
        do @(vif.mst_cb);
        while (!vif.mst_cb.bvalid);
        item.resp = vif.mst_cb.bresp;
        vif.mst_cb.bready <= 1'b0;

        `uvm_info(get_type_name(), $sformatf("WRITE: %s", item.convert2string()), UVM_MEDIUM)
    endtask

    task drive_read(axi4lite_seq_item item);
        // Drive AR channel
        @(vif.mst_cb);
        vif.mst_cb.arvalid <= 1'b1;
        vif.mst_cb.araddr  <= item.addr;
        vif.mst_cb.arprot  <= item.prot;
        do @(vif.mst_cb);
        while (!vif.mst_cb.arready);
        vif.mst_cb.arvalid <= 1'b0;

        // Wait for R channel response
        vif.mst_cb.rready <= 1'b1;
        do @(vif.mst_cb);
        while (!vif.mst_cb.rvalid);
        item.rdata = vif.mst_cb.rdata;
        item.resp  = vif.mst_cb.rresp;
        vif.mst_cb.rready <= 1'b0;

        `uvm_info(get_type_name(), $sformatf("READ:  %s", item.convert2string()), UVM_MEDIUM)
    endtask

endclass
