// AXI4-Lite Sequences

// Write sequence
class axi4lite_write_seq extends uvm_sequence #(axi4lite_seq_item);

    `uvm_object_utils(axi4lite_write_seq)

    int num_txns = 1;

    function new(string name = "axi4lite_write_seq");
        super.new(name);
    endfunction

    task body();
        axi4lite_seq_item item;
        int count_arg;

        if ($value$plusargs("COUNT=%d", count_arg))
            num_txns = count_arg;

        `uvm_info(get_type_name(), $sformatf("Running %0d writes", num_txns), UVM_LOW)

        for (int i = 0; i < num_txns; i++) begin
            item = axi4lite_seq_item::type_id::create($sformatf("wr_item_%0d", i));
            start_item(item);
            if (!item.randomize() with {
                txn_type == axi4lite_seq_item::WRITE;
                addr == (i % 16) * 4;
            }) `uvm_error(get_type_name(), "Randomization failed")
            finish_item(item);
        end
    endtask

endclass

// Read sequence
class axi4lite_read_seq extends uvm_sequence #(axi4lite_seq_item);

    `uvm_object_utils(axi4lite_read_seq)

    int num_txns = 1;

    function new(string name = "axi4lite_read_seq");
        super.new(name);
    endfunction

    task body();
        axi4lite_seq_item item;
        int count_arg;

        if ($value$plusargs("COUNT=%d", count_arg))
            num_txns = count_arg;

        `uvm_info(get_type_name(), $sformatf("Running %0d reads", num_txns), UVM_LOW)

        for (int i = 0; i < num_txns; i++) begin
            item = axi4lite_seq_item::type_id::create($sformatf("rd_item_%0d", i));
            start_item(item);
            if (!item.randomize() with {
                txn_type == axi4lite_seq_item::READ;
                addr == (i % 16) * 4;
            }) `uvm_error(get_type_name(), "Randomization failed")
            finish_item(item);
        end
    endtask

endclass

// Write-then-read-back sequence (directed test)
class axi4lite_wr_rd_seq extends uvm_sequence #(axi4lite_seq_item);

    `uvm_object_utils(axi4lite_wr_rd_seq)

    int num_txns = 1;

    function new(string name = "axi4lite_wr_rd_seq");
        super.new(name);
    endfunction

    task body();
        axi4lite_seq_item item;
        int count_arg;

        if ($value$plusargs("COUNT=%d", count_arg))
            num_txns = count_arg;

        `uvm_info(get_type_name(), $sformatf("Running %0d write-read pairs", num_txns), UVM_LOW)

        for (int i = 0; i < num_txns; i++) begin
            item = axi4lite_seq_item::type_id::create($sformatf("wr_item_%0d", i));
            start_item(item);
            if (!item.randomize() with {
                txn_type == axi4lite_seq_item::WRITE;
                addr == (i % 16) * 4;
            }) `uvm_error(get_type_name(), "Randomization failed")
            finish_item(item);
        end

        // Read back all written addresses
        for (int i = 0; i < num_txns; i++) begin
            item = axi4lite_seq_item::type_id::create($sformatf("rd_item_%0d", i));
            start_item(item);
            if (!item.randomize() with {
                txn_type == axi4lite_seq_item::READ;
                addr == (i % 16) * 4;
            }) `uvm_error(get_type_name(), "Randomization failed")
            finish_item(item);
        end
    endtask

endclass

// Random traffic sequence
class axi4lite_random_seq extends uvm_sequence #(axi4lite_seq_item);

    `uvm_object_utils(axi4lite_random_seq)

    int num_txns = 1;

    function new(string name = "axi4lite_random_seq");
        super.new(name);
    endfunction

    task body();
        axi4lite_seq_item item;
        int count_arg;

        if ($value$plusargs("COUNT=%d", count_arg))
            num_txns = count_arg;

        `uvm_info(get_type_name(), $sformatf("Running %0d random transactions", num_txns), UVM_LOW)

        for (int i = 0; i < num_txns; i++) begin
            item = axi4lite_seq_item::type_id::create($sformatf("rand_item_%0d", i));
            start_item(item);
            if (!item.randomize() with {
                addr inside {[0:60]};  // Limit to DUT's 16 registers (0x00-0x3C)
                addr[1:0] == 2'b00;    // Word-aligned
            }) `uvm_error(get_type_name(), "Randomization failed")
            finish_item(item);
        end
    endtask

endclass
