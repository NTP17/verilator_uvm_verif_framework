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
        @(posedge vif.clk);

        forever begin
            seq_item_port.get_next_item(item);
            drive_item(item);
            seq_item_port.item_done();
        end
    endtask

    task reset_signals();
        vif.awvalid <= 1'b0;
        vif.awaddr  <= '0;
        vif.awprot  <= '0;
        vif.wvalid  <= 1'b0;
        vif.wdata   <= '0;
        vif.wstrb   <= '0;
        vif.bready  <= 1'b0;
        vif.arvalid <= 1'b0;
        vif.araddr  <= '0;
        vif.arprot  <= '0;
        vif.rready  <= 1'b0;
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
                @(posedge vif.clk);
                vif.awvalid <= 1'b1;
                vif.awaddr  <= item.addr;
                vif.awprot  <= item.prot;
                do @(posedge vif.clk);
                while (!vif.awready);
                vif.awvalid <= 1'b0;
            end
            begin : w_channel
                @(posedge vif.clk);
                vif.wvalid <= 1'b1;
                vif.wdata  <= item.data;
                vif.wstrb  <= item.strb;
                do @(posedge vif.clk);
                while (!vif.wready);
                vif.wvalid <= 1'b0;
            end
        join

        // Wait for B channel response
        vif.bready <= 1'b1;
        do @(posedge vif.clk);
        while (!vif.bvalid);
        item.resp = vif.bresp;
        vif.bready <= 1'b0;

        `uvm_info(get_type_name(), $sformatf("WRITE: %s", item.convert2string()), UVM_MEDIUM)
    endtask

    task drive_read(axi4lite_seq_item item);
        // Drive AR channel
        @(posedge vif.clk);
        vif.arvalid <= 1'b1;
        vif.araddr  <= item.addr;
        vif.arprot  <= item.prot;
        do @(posedge vif.clk);
        while (!vif.arready);
        vif.arvalid <= 1'b0;

        // Wait for R channel response
        vif.rready <= 1'b1;
        do @(posedge vif.clk);
        while (!vif.rvalid);
        item.rdata = vif.rdata;
        item.resp  = vif.rresp;
        vif.rready <= 1'b0;

        `uvm_info(get_type_name(), $sformatf("READ:  %s", item.convert2string()), UVM_MEDIUM)
    endtask

endclass
