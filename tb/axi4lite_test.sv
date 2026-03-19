// AXI4-Lite UVM Test
class axi4lite_base_test extends uvm_test;

    `uvm_component_utils(axi4lite_base_test)

    axi4lite_env env;

    function new(string name = "axi4lite_base_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        env = axi4lite_env::type_id::create("env", this);
    endfunction

    function void end_of_elaboration_phase(uvm_phase phase);
        super.end_of_elaboration_phase(phase);
        uvm_top.print_topology();
    endfunction

endclass

// Write-only test
class axi4lite_write_test extends axi4lite_base_test;

    `uvm_component_utils(axi4lite_write_test)

    function new(string name = "axi4lite_write_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        axi4lite_write_seq seq;

        phase.raise_objection(this);
        seq = axi4lite_write_seq::type_id::create("seq");
        seq.start(env.agent.sqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass

// Read-only test
class axi4lite_read_test extends axi4lite_base_test;

    `uvm_component_utils(axi4lite_read_test)

    function new(string name = "axi4lite_read_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        axi4lite_read_seq seq;

        phase.raise_objection(this);
        seq = axi4lite_read_seq::type_id::create("seq");
        seq.start(env.agent.sqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass

// Directed write-read-back test
class axi4lite_wr_rd_test extends axi4lite_base_test;

    `uvm_component_utils(axi4lite_wr_rd_test)

    function new(string name = "axi4lite_wr_rd_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        axi4lite_wr_rd_seq seq;

        phase.raise_objection(this);
        seq = axi4lite_wr_rd_seq::type_id::create("seq");
        seq.start(env.agent.sqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass

// Random traffic test
class axi4lite_random_test extends axi4lite_base_test;

    `uvm_component_utils(axi4lite_random_test)

    function new(string name = "axi4lite_random_test", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        axi4lite_random_seq seq;

        phase.raise_objection(this);
        seq = axi4lite_random_seq::type_id::create("seq");
        seq.start(env.agent.sqr);
        #100;
        phase.drop_objection(this);
    endtask

endclass
