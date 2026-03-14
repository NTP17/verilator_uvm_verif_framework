// AXI4-Lite Environment
class axi4lite_env extends uvm_env;

    `uvm_component_utils(axi4lite_env)

    axi4lite_agent      agent;
    axi4lite_scoreboard scb;
    axi4lite_coverage   cov;

    function new(string name = "axi4lite_env", uvm_component parent = null);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        agent = axi4lite_agent::type_id::create("agent", this);
        scb   = axi4lite_scoreboard::type_id::create("scb", this);
        cov   = axi4lite_coverage::type_id::create("cov", this);
    endfunction

    function void connect_phase(uvm_phase phase);
        super.connect_phase(phase);
        agent.ap.connect(scb.analysis_export);
        agent.ap.connect(cov.analysis_export);
    endfunction

endclass
