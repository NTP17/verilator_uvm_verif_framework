// ==========================================================================
//  SVA + Coverage Engine — SystemVerilog DPI-C Import Package
//  This package declares all DPI-C functions that bridge SV to the C++
//  engine.  Preprocessor-generated code (svpp.py) uses these imports.
// ==========================================================================
package sva_dpi_pkg;

    // ---------------------------------------------------------------
    //  Initialization / Finalization
    // ---------------------------------------------------------------
    import "DPI-C" function void sva_init();
    import "DPI-C" function void sva_final();
    import "DPI-C" function void sva_reset();

    // ---------------------------------------------------------------
    //  Signal Management
    // ---------------------------------------------------------------
    import "DPI-C" function void sva_set(
        input string signal_name, input longint value);

    // ---------------------------------------------------------------
    //  Assertion Management
    // ---------------------------------------------------------------
    //  kind: 0 = assert, 1 = cover, 2 = assume
    import "DPI-C" function int sva_assert_create(
        input string name, input string prop_expr, input int kind);

    import "DPI-C" function int sva_assert_create_ex(
        input string name, input string prop_expr, input int kind,
        input string file, input int line);

    import "DPI-C" function void sva_assert_set_message(
        input int id, input string msg);

    import "DPI-C" function void sva_assert_enable(
        input int id, input int enable);

    // Tick: evaluate all registered assertions with current signal values
    import "DPI-C" function void sva_tick(input longint sim_time);

    // Query assertion results
    import "DPI-C" function longint sva_assert_pass_count(input int id);
    import "DPI-C" function longint sva_assert_fail_count(input int id);
    import "DPI-C" function longint sva_assert_vacuous_count(input int id);

    // ---------------------------------------------------------------
    //  Covergroup Management
    // ---------------------------------------------------------------
    import "DPI-C" function int sva_cg_create(input string name);

    import "DPI-C" function int sva_cg_add_coverpoint(
        input int cg_id, input string cp_name);

    import "DPI-C" function void sva_cg_add_bin(
        input int cp_id, input string bin_name,
        input longint lo, input longint hi);

    // Transition bins: pass sequence as open array
    import "DPI-C" function void sva_cg_add_transition_bin(
        input int cp_id, input string bin_name,
        input longint seq[], input int len);

    // Wildcard bins: mask & pattern  (val & mask == pattern)
    import "DPI-C" function void sva_cg_add_wildcard_bin(
        input int cp_id, input string bin_name,
        input longint mask, input longint pattern);

    // Illegal / Ignore / Default bins
    import "DPI-C" function void sva_cg_add_illegal_bin(
        input int cp_id, input string bin_name,
        input longint lo, input longint hi);

    import "DPI-C" function void sva_cg_add_ignore_bin(
        input int cp_id, input string bin_name,
        input longint lo, input longint hi);

    import "DPI-C" function void sva_cg_add_default_bin(
        input int cp_id, input string bin_name);

    import "DPI-C" function void sva_cg_add_auto_bins(
        input int cp_id, input int count);

    // Cross coverage
    import "DPI-C" function int sva_cg_add_cross2(
        input int cg_id, input string name,
        input int cp_a, input int cp_b);

    import "DPI-C" function int sva_cg_add_cross3(
        input int cg_id, input string name,
        input int a, input int b, input int c);

    // Sample a coverpoint value
    import "DPI-C" function void sva_cg_sample_point(
        input int cp_id, input longint value);

    // Finalize sampling for a covergroup (computes cross bins)
    import "DPI-C" function void sva_cg_sample(input int cg_id);

    // Query overall coverage %
    import "DPI-C" function real sva_cg_coverage(input int cg_id);

    // Print full report
    import "DPI-C" function void sva_report();

    // Save DPI scope for UVM error callback
    import "DPI-C" context function void sva_register_uvm_scope();

    // ---------------------------------------------------------------
    //  VPI Signal Driver (Verilator VIF workaround)
    // ---------------------------------------------------------------
    import "DPI-C" function void svpp_vpi_drive(
        input string signal_path, input int value, input int width);

endpackage : sva_dpi_pkg
