# Verilator + UVM Verification Framework

A reusable framework for running UVM testbenches on Verilator with native SystemVerilog assertion (SVA) and functional coverage support via a C++ NFA engine and DPI-C bridge.

## Typical Project Structure

```
.
├── Makefile              # Project-agnostic build system
├── filelist.f            # Project-specific source list
├── lib/                  # Reusable SVA/coverage engine (copy to any project)
│   ├── sva_engine.h      #   C++ NFA-based SVA evaluation engine
│   ├── sva_engine.cpp    #   Engine implementation
│   ├── sva_dpi.cpp       #   DPI-C bridge (SV <-> C++)
│   ├── sva_dpi_pkg.sv    #   SV package declaring all DPI-C imports
│   └── sva_uvm_report.svh #  UVM error reporting bridge (include in tb_top)
├── tools/                # Reusable preprocessor (copy to any project)
│   └── svpp.py           #   Source-to-source transformer for SVA + covergroup
├── gen/                  # Auto-generated preprocessed files (disposable)
├── obj_dir/              # Verilator build output (disposable)
├── rtl/                  # RTL design files
├── vip/                  # VIP: interface, package, sequences, UVM components
├── tb/                   # Testbench top, tests, regression list
│   ├── tb_top.sv
└── └── regression.list
```

## Quick Start

```bash
make compile                # Preprocess + compile
make run                    # Run default test
make all                    # Compile then Run
make regress                # Run all tests within a regression list (see Runtime Options below)
make clean                  # Remove all generated files
```

## Makefile Targets

| Target    | Description |
|-----------|-------------|
| `compile` | Preprocess all `.sv` files through `svpp.py`, then compile with Verilator |
| `run`     | Run a single simulation |
| `all`     | Similar to `make compile && make run`. This is the default `make` behavior. |
| `regress` | Run all tests in `REGRESS_LIST`, report pass/fail summary |
| `pp`      | Preprocess a single file: `make pp SRC=path/to/file.sv` |
| `clean`   | Remove `obj_dir/`, `gen/`, logs, coverage data, and reports |

## Runtime Options

All options can be combined: `make run TEST=my_test COUNT=50 SEED=42 LOG=1 DUMP=1`

| Variable | Default | Description |
|----------|---------|-------------|
| `TEST`   | *(set in Makefile)* | UVM test name (`+UVM_TESTNAME`) |
| `COUNT`  | `1` | Transaction count per test (`+COUNT=N`) |
| `SEED`   | `random` | Random seed; set to a number for reproducibility (`+verilator+seed+N`) |
| `DUMP`   | `0` | Set `1` to enable VCD waveform dump (`dump.vcd`) |
| `LOG`    | `0` | Set `1` to tee stdout+stderr into `<testname>.log` |
| `COVER`  | `0` | Set `1` to run `verilator_coverage` annotation after simulation |
| `REGRESS_LIST` | `tb/regression.list` | Path to regression test list file |

## Regression Examples

```bash
make regress                                        # Run all tests, random seed
make regress COUNT=50 SEED=42                       # Fixed seed, 50 transactions each
make regress COVER=1 LOG=1                          # With coverage merge and per-test logs
make regress REGRESS_LIST=tb/smoke.list             # Use a custom test list
make regress REGRESS_LIST=tb/nightly.list COUNT=100 # Nightly regression
```

- Tests are listed in a regression file (default: `tb/regression.list`), one test name per line
- Blank lines and `#` comments are allowed in the list file
- Override the list with `REGRESS_LIST=path/to/file`
- Each test is checked for `UVM_ERROR` / `UVM_FATAL` in the output to determine pass/fail
- Summary is printed to terminal and written to `regress_report.txt`
- The random seed used is displayed per-test for reproducibility
- With `COVER=1`, per-test `.dat` files are merged and annotated at the end

## SVA Preprocessor (`svpp.py`)

The preprocessor transforms native SystemVerilog syntax into DPI-C calls that Verilator can compile.

### Supported Constructs

**Assertions** (in modules):
```systemverilog
my_assert: assert property (@(posedge clk) disable iff (!rst_n)
    valid && !ready |-> ##1 valid
) else `uvm_error("my_assert", "valid must stay high until ready");
```

**Cover properties** (in modules):
```systemverilog
my_cover: cover property (@(posedge clk) disable iff (!rst_n)
    req ##[1:3] ack
);
```

**Covergroups** (in modules or classes):
```systemverilog
covergroup my_cg @(posedge clk);
    addr_cp: coverpoint addr[3:0] {
        bins low  = {[0:7]};
        bins high = {[8:15]};
    }
    data_cp: coverpoint data {
        bins zeros = {0};
        bins ones  = {'1};
        bins others = default;
        illegal_bins bad = {32'hDEAD};
    }
    addr_x_data: cross addr_cp, data_cp;
endgroup
```

For class-based covergroups (e.g. inside a `uvm_subscriber`), `svpp.py` generates init/sample functions instead of `initial`/`always` blocks. The `new()` and `.sample()` calls are automatically rewritten.

### Preprocessing Flow

1. All `.sv` files listed in `filelist.f` are preprocessed into `gen/`
2. Additional `.sv` files found in `+incdir` directories are also preprocessed (for `` `include``'d files)
3. Preprocessed files (`.pp.sv`) are passed to Verilator for compilation
4. Included files are found by Verilator via `+incdir+gen`

## SVA Engine

The C++ NFA engine (`lib/sva_engine.cpp`) evaluates assertions and collects coverage at runtime:

- **Assertions**: Evaluated every clock tick via `sva_tick()`. Failures are reported as `UVM_ERROR` through a DPI-C callback to UVM's reporting infrastructure.
- **Coverage**: Sampled via `sva_cg_sample_point()` / `sva_cg_sample()`. Reports are written to `sva_report.txt` at simulation end.

## Setting Up a New Project

1. Copy `lib/`, `tools/`, and `Makefile` to your new project directory

2. Set `UVM_HOME` in the Makefile (or export it as an environment variable)

3. Update `TEST ?=` in the Makefile to your default test name

4. Create `filelist.f` listing your sources, include paths, and top module:
   ```
   // Include directories
   +incdir+src
   +incdir+tb
   +incdir+lib
   +incdir+gen

   // Top module
   --top-module tb_top

   // SVA / Coverage engine
   lib/sva_dpi_pkg.sv

   // Design + VIP
   src/my_rtl.sv
   src/my_pkg.sv

   // Testbench
   tb/tb_top.sv
   ```
   - Files listed here are preprocessed into `gen/` and compiled by Verilator
   - Files in `+incdir` paths that are `` `include``'d by packages are auto-discovered and preprocessed, but not compiled as top-level sources
   - Always include `+incdir+gen` so Verilator can find preprocessed include files

5. Create a regression list file (e.g. `tb/regression.list`):
   ```
   # Smoke tests
   my_basic_test
   my_random_test

   # Corner cases
   my_error_inject_test
   ```

6. In your `tb_top.sv`, add the SVA/UVM bridge:
   ```systemverilog
   import sva_dpi_pkg::*;
   `include "sva_uvm_report.svh"

   initial begin
       sva_register_uvm_scope();
       run_test();
   end

   final begin
       sva_final();
   end
   ```

7. Run:
   ```bash
   make regress                  # Run default regression list
   make all TEST=my_basic_test   # Run a single test
   ```

## Reports

| File | Content |
|------|---------|
| `sva_report.txt` | Assertion pass/fail/vacuous counts + coverage bin hit summary |
| `regress_report.txt` | Per-test pass/fail + regression summary |
| `<testname>.log` | Full simulation output (when `LOG=1`) |
| `coverage_annotated/` | Source-annotated coverage (when `COVER=1`) |

## Dependencies

- [Verilator](https://verilator.org/) (tested with v5.046)
- UVM (IEEE 1800.2-2020 compatible, with DPI support)
- Python 3 (for `svpp.py`)
- GNU Make
