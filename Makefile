## Verilator + UVM Makefile (project-agnostic)
## Project-specific sources are listed in FILELIST.
## All .sv files are preprocessed through svpp into PP_DIR.

UVM_HOME      = /home/truefu/uvm-verilator
#UVM_HOME      = /home/truefu/uvm-verilator-uvm-1.1d
UVM_PKG       = $(UVM_HOME)/src/uvm_pkg.sv
UVM_DPI_DIR   = $(UVM_HOME)/src/dpi
UVM_DPI_SRC   = $(UVM_DPI_DIR)/uvm_dpi.cc

# SVA + Coverage engine library
SVA_ENGINE    = lib/sva_engine.cpp
SVA_DPI       = lib/sva_dpi.cpp
VPI_DRIVE     = lib/svpp_vpi_drive.cpp
SVPP_BIN      = tools/svpp
MERGE_SVA     = tools/merger

# Auto-detect clock half-period from source files for the 'output negedge'
# workaround.  Scans all SV/V sources for common clock generation patterns:
#   #(NAME/2) clk = ~clk;       →  resolves NAME from any declaration, /2
#   #(`NAME/2) clk = ~clk;      →  resolves `define NAME, /2
#   #5 clk = ~clk;              →  uses the literal directly
# NAME can be declared as parameter, localparam, `define, or a bare
# typed variable (int, time, realtime, logic, bit, reg, integer, etc.).
# Override: make ... CLK_HALF_PERIOD=10
CLK_HALF_PERIOD ?= $(strip $(shell \
  all_files="$(SV_SRCS) $(V_SRCS) $(INC_SVS)"; \
  for f in $$all_files; do cat "$$f" 2>/dev/null; done | \
  perl -0777 -ne ' \
    if (/\#\s*\(\s*`?(\w+)\s*\/\s*2\s*\)\s*\w+\s*=\s*~\s*\w+/) { \
      $$p=$$1; \
      if (/`define\s+$$p\s+(\d+)/) { print int($$1/2); exit } \
      if (/(?:parameter|localparam|int|time|realtime|logic|bit|reg|integer)\b[^;]*?\b$$p\s*=\s*(\d+)/) \
        { print int($$1/2); exit } \
    } \
    if (/\#\s*(\d+)\s+\w+\s*=\s*~\s*\w+/) { print $$1; exit } \
    print 5' \
))
SVPP          = $(SVPP_BIN) --clk-half-period $(CLK_HALF_PERIOD)

# Project file list and preprocessor output directory
FILELIST     ?= filelist.f
PP_DIR        = gen

# Recursively flatten -f includes so nested file lists are resolved
_flat_cmd = awk 'function proc(f, line,a){while((getline line<f)>0){if(line~/^-f[[:space:]]/){split(line,a);proc(a[2])}else print line}close(f)}BEGIN{proc("$(FILELIST)")}'

# Extract all .sv source paths from filelist (skip comments, flags, blank lines)
SV_SRCS      := $(shell $(_flat_cmd) | grep -E '\.sv$$' | grep -v '^//')
# Preprocessed targets: gen/<basename>.pp.sv
SV_PPS       := $(foreach s,$(SV_SRCS),$(PP_DIR)/$(notdir $(basename $(s))).pp.sv)
# Auto-discover included .sv files from +incdir paths (preprocessed but not compiled)
INC_DIRS     := $(filter-out $(PP_DIR),$(patsubst +incdir+%,%,$(shell $(_flat_cmd) | grep -oP '\+incdir\+\S+')))
INC_SVS      := $(filter-out $(SV_SRCS),$(wildcard $(addsuffix /*.sv,$(INC_DIRS))))
INC_PPS      := $(foreach s,$(INC_SVS),$(PP_DIR)/$(notdir $(basename $(s))).pp.sv)
# Extract .v source paths — preprocessed to gen/ with original name so +incdir+gen
# shadows the originals (tristate, etc. are rewritten for Verilator)
V_SRCS       := $(shell $(_flat_cmd) | grep -E '\.v$$' | grep -v '^//')
V_PPS        := $(foreach s,$(V_SRCS),$(PP_DIR)/$(notdir $(s)))
# Non-.sv flags/options from filelist (incdirs, --top-module, etc.)
FLIST_FLAGS  := $(shell $(_flat_cmd) | grep -E '^\+|^-' | grep -v '^-f[[:space:]]')
# Derive executable name from --top-module in filelist
TOP_MODULE   := $(shell $(_flat_cmd) | grep -oP '(?<=--top-module\s)\S+')
SIM_EXE       = obj_dir/V$(TOP_MODULE)

# Runtime options
DUMP         ?= 0
LOG          ?= 0
COVER        ?= 0
COUNT        ?= 1
SEED         ?= random
# Default test
TEST         ?= axi4lite_wr_rd_test
# Regression list
REGRESS_LIST  ?= tb/regression.list

# Verilator flags
VERILATOR     = verilator
VFLAGS        = --binary --timing --trace
VFLAGS       += -j 0
VFLAGS       += -CFLAGS "-I$(UVM_DPI_DIR) -I$(CURDIR)/lib -DVERILATOR -std=c++17"
VFLAGS       += --vpi
VFLAGS       += --assert
VFLAGS       += -Wno-fatal
VFLAGS       += +incdir+$(UVM_HOME)/src

ifeq ($(SEED),random)
SEED_VAL     := $(shell shuf -i 1-2147483647 -n 1)
else
SEED_VAL     := $(SEED)
endif
VFLAGS       += +define+COUNT=$(COUNT)
PLUSARGS      = +UVM_TESTNAME=$(TEST) +UVM_VERBOSITY=UVM_MEDIUM +verilator+seed+$(SEED_VAL)
ifneq ($(DUMP),0)
PLUSARGS      += +dump
endif
ifneq ($(COVER),0)
PLUSARGS      += +coverage
endif

# SVA report path (written by sva_final() in the sim when +coverage is passed)
SVA_REPORT    = sva_report.txt

# Pipe helper — wraps a command to tee into <test>.log when LOG=1
# Usage: $(call LOG_CMD,<testname>)
ifneq ($(LOG),0)
LOG_CMD = | tee $(1).log
else
LOG_CMD =
endif

.PHONY: all compile run clean regress pp

all: compile run

compile: $(SV_PPS) $(INC_PPS) $(V_PPS)
	$(VERILATOR) $(VFLAGS) \
		$(FLIST_FLAGS) \
		$(UVM_PKG) \
		$(SV_PPS) \
		$(UVM_DPI_SRC) \
		$(SVA_ENGINE) \
		$(SVA_DPI) \
		$(VPI_DRIVE)

run:
	@{ echo "SEED: $(SEED_VAL)"; ./$(SIM_EXE) $(PLUSARGS); } 2>&1 $(call LOG_CMD,$(TEST))

REGRESS_RPT   = regress_report.txt

REGRESS_LOG   = regress.log

regress: compile
	@rpt=$(REGRESS_RPT); \
	rlog=$(REGRESS_LOG); \
	pass=0; fail=0; num=0; \
	total=$$(grep -cvE '^\s*(\#|$$)' $(REGRESS_LIST)); \
	sva_tmp=$$(mktemp -d); \
	: > $$rpt; : > $$rlog; rm -f $(SVA_REPORT); \
	echo "Regression: seed=$(SEED_VAL) count=$(COUNT) $$(date)" >> $$rlog; \
	while IFS= read -r t || [ -n "$$t" ]; do \
		case "$$t" in \#*|"") continue;; esac; \
		num=$$((num + 1)); \
		printf "Running test %d of %d: %s... " $$num $$total "$$t"; \
		echo "" >> $$rlog; \
		echo "======== [$$num] $$t (seed=$(SEED_VAL)) ========" >> $$rlog; \
		sim_out=$$({ ./$(SIM_EXE) +UVM_TESTNAME=$$t +UVM_VERBOSITY=UVM_MEDIUM +verilator+seed+$(SEED_VAL) $(if $(filter-out 0,$(COVER)),+coverage) $(if $(filter-out 0,$(DUMP)),+dump) ; } 2>&1); \
		echo "$$sim_out" >> $$rlog; \
		if [ "$(LOG)" != "0" ]; then echo "$$sim_out" > $$t.log; fi; \
		if echo "$$sim_out" | grep -qE 'UVM_(ERROR|FATAL) : +[1-9]'; then \
			fail=$$((fail + 1)); \
			echo "FAIL"; \
			echo "  FAIL  $$t" >> $$rpt; \
			echo "-------- $$t: FAIL --------" >> $$rlog; \
		else \
			pass=$$((pass + 1)); \
			echo "PASS"; \
			echo "  PASS  $$t" >> $$rpt; \
			echo "-------- $$t: PASS --------" >> $$rlog; \
		fi; \
		if [ -f $(SVA_REPORT) ]; then \
			mv $(SVA_REPORT) "$$sva_tmp/$$t.txt"; \
		fi; \
	done < $(REGRESS_LIST); \
	echo ""; \
	echo "======== REGRESSION SUMMARY ========"; \
	echo "  Total: $$total  Pass: $$pass  Fail: $$fail"; \
	echo "===================================="; \
	echo "" >> $$rpt; \
	echo "======== REGRESSION SUMMARY ========" >> $$rpt; \
	echo "  Total: $$total  Pass: $$pass  Fail: $$fail" >> $$rpt; \
	echo "====================================" >> $$rpt; \
	sva_files=$$(ls "$$sva_tmp"/*.txt 2>/dev/null); \
	if [ -n "$$sva_files" ]; then \
		$(MERGE_SVA) $$sva_files -o $(SVA_REPORT); \
		echo "SVA/Coverage report: $(SVA_REPORT)"; \
	fi; \
	rm -rf "$$sva_tmp"; \
	echo "Log: $$rlog  Report: $$rpt"; \
	[ $$fail -eq 0 ]

# ---------------------------------------------------------------
#  Preprocessor targets
# ---------------------------------------------------------------

# Run preprocessor on a single file:  make pp SRC=path/to/file.sv
SRC ?=
pp:
ifdef SRC
	$(SVPP) $(SRC)
else
	@echo "Usage: make pp SRC=path/to/file.sv"
endif

# Generate per-file preprocess rules: gen/<name>.pp.sv depends on <src>.sv
define SVPP_RULE
$(PP_DIR)/$(notdir $(basename $(1))).pp.sv: $(1) $(SVPP_BIN) | $(PP_DIR)
	$$(SVPP) $(1) -o $$@
endef
$(foreach s,$(SV_SRCS),$(eval $(call SVPP_RULE,$(s))))
$(foreach s,$(INC_SVS),$(eval $(call SVPP_RULE,$(s))))

# Verilog (.v) preprocessing rules: gen/<name>.v (keep original name)
define VPP_RULE
$(PP_DIR)/$(notdir $(1)): $(1) $(SVPP_BIN) | $(PP_DIR)
	$$(SVPP) $(1) -o $$@
endef
$(foreach s,$(V_SRCS),$(eval $(call VPP_RULE,$(s))))

$(PP_DIR):
	mkdir -p $(PP_DIR)

clean:
	rm -rf obj_dir $(PP_DIR) *.vcd \
		$(SVA_REPORT) *.log $(REGRESS_RPT)
