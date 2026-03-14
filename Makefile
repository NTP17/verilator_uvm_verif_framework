## Verilator + UVM Makefile (project-agnostic)
## Project-specific sources are listed in FILELIST.
## All .sv files are preprocessed through svpp.py into PP_DIR.

UVM_HOME     ?= /home/truefu/uvm
UVM_PKG       = $(UVM_HOME)/uvm_pkg_all_v2020_3_1_dpi.svh
UVM_DPI_DIR   = $(UVM_HOME)/v2020_3_1/dpi
UVM_DPI_SRC   = $(UVM_DPI_DIR)/uvm_dpi.cc

# SVA + Coverage engine library
SVA_ENGINE    = lib/sva_engine.cpp
SVA_DPI       = lib/sva_dpi.cpp
SVPP          = python3 tools/svpp.py

# Project file list and preprocessor output directory
FILELIST     ?= filelist.f
PP_DIR        = gen

# Extract all .sv source paths from filelist (skip comments, flags, blank lines)
SV_SRCS      := $(shell grep -E '\.sv$$' $(FILELIST) | grep -v '^//')
# Preprocessed targets: gen/<basename>.pp.sv
SV_PPS       := $(foreach s,$(SV_SRCS),$(PP_DIR)/$(notdir $(basename $(s))).pp.sv)
# Auto-discover included .sv files from +incdir paths (preprocessed but not compiled)
INC_DIRS     := $(patsubst +incdir+%,%,$(shell grep -oP '\+incdir\+\S+' $(FILELIST)))
INC_SVS      := $(filter-out $(SV_SRCS),$(wildcard $(addsuffix /*.sv,$(INC_DIRS))))
INC_PPS      := $(foreach s,$(INC_SVS),$(PP_DIR)/$(notdir $(basename $(s))).pp.sv)
# Non-.sv flags/options from filelist (incdirs, --top-module, etc.)
FLIST_FLAGS  := $(shell grep -E '^\+|^--' $(FILELIST))

# Verilator flags
VERILATOR     = verilator
VFLAGS        = --binary --timing --trace
VFLAGS       += -j 0
VFLAGS       += -CFLAGS "-I$(UVM_DPI_DIR) -I$(CURDIR)/lib -DVERILATOR -std=c++17"
VFLAGS       += --vpi
VFLAGS       += --assert
VFLAGS       += --coverage-user
VFLAGS       += -Wno-fatal

# Default test
TEST         ?= axi4lite_wr_rd_test

# Runtime options
DUMP         ?= 0
LOG          ?= 0
COVER        ?= 0
COUNT        ?= 0
SEED         ?= random
ifeq ($(SEED),random)
SEED_VAL     := $(shell shuf -i 1-2147483647 -n 1)
else
SEED_VAL     := $(SEED)
endif
PLUSARGS      = +UVM_TESTNAME=$(TEST) +UVM_VERBOSITY=UVM_MEDIUM +verilator+seed+$(SEED_VAL)
ifneq ($(DUMP),0)
PLUSARGS      += +dump
endif
ifneq ($(COUNT),0)
PLUSARGS      += +COUNT=$(COUNT)
endif

# Pipe helper — wraps a command to tee into <test>.log when LOG=1
# Usage: $(call LOG_CMD,<testname>)
ifneq ($(LOG),0)
LOG_CMD = | tee $(1).log
else
LOG_CMD =
endif

# Coverage annotation after sim when COVER=1
ifneq ($(COVER),0)
define COVER_CMD
	verilator_coverage --annotate coverage_annotated coverage.dat
	verilator_coverage coverage.dat
endef
else
define COVER_CMD
endef
endif

# Regression list
REGRESS_LIST  ?= tb/regression.list

.PHONY: all compile run clean regress pp

all: compile run

compile: $(SV_PPS) $(INC_PPS)
	$(VERILATOR) $(VFLAGS) \
		$(FLIST_FLAGS) \
		$(UVM_PKG) \
		$(SV_PPS) \
		$(UVM_DPI_SRC) \
		$(SVA_ENGINE) \
		$(SVA_DPI)

run:
	@{ echo "SEED: $(SEED_VAL)"; ./obj_dir/Vtb_top $(PLUSARGS); } 2>&1 $(call LOG_CMD,$(TEST))
	$(COVER_CMD)

REGRESS_RPT   = regress_report.txt

regress: compile
	@rpt=$(REGRESS_RPT); \
	pass=0; fail=0; total=0; \
	: > $$rpt; \
	log() { echo "$$1"; echo "$$1" >> $$rpt; }; \
	log "Regression: seed=$(SEED_VAL) count=$(COUNT) $$(date)"; \
	while IFS= read -r t || [ -n "$$t" ]; do \
		case "$$t" in \#*|"") continue;; esac; \
		total=$$((total + 1)); \
		log ""; \
		log "======== [$$total] $$t (seed=$(SEED_VAL)) ========"; \
		sim_out=$$({ ./obj_dir/Vtb_top +UVM_TESTNAME=$$t +UVM_VERBOSITY=UVM_MEDIUM +verilator+seed+$(SEED_VAL) $(if $(filter-out 0,$(COUNT)),+COUNT=$(COUNT)); } 2>&1); \
		echo "$$sim_out" $(call LOG_CMD,$$t); \
		if echo "$$sim_out" | grep -qE 'UVM_(ERROR|FATAL) : +[1-9]'; then \
			fail=$$((fail + 1)); \
			log "-------- $$t: FAIL --------"; \
		else \
			pass=$$((pass + 1)); \
			log "-------- $$t: PASS --------"; \
		fi; \
		if [ "$(COVER)" != "0" ] && [ -f coverage.dat ]; then \
			mv coverage.dat coverage_$$t.dat; \
		fi; \
	done < $(REGRESS_LIST); \
	log ""; \
	log "======== REGRESSION SUMMARY ========"; \
	log "  Total: $$total  Pass: $$pass  Fail: $$fail"; \
	log "===================================="; \
	if [ "$(COVER)" != "0" ]; then \
		cov_files=$$(ls coverage_*.dat 2>/dev/null); \
		if [ -n "$$cov_files" ]; then \
			log ""; \
			log "Merging coverage: $$cov_files"; \
			verilator_coverage --annotate coverage_annotated $$cov_files; \
			verilator_coverage $$cov_files; \
		fi; \
	fi; \
	echo "Report: $$rpt"; \
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
$(PP_DIR)/$(notdir $(basename $(1))).pp.sv: $(1) tools/svpp.py | $(PP_DIR)
	$$(SVPP) $(1) -o $$@
endef
$(foreach s,$(SV_SRCS),$(eval $(call SVPP_RULE,$(s))))
$(foreach s,$(INC_SVS),$(eval $(call SVPP_RULE,$(s))))

$(PP_DIR):
	mkdir -p $(PP_DIR)

clean:
	rm -rf obj_dir $(PP_DIR) dump.vcd coverage*.dat coverage_annotated \
		covergroup_report.txt sva_report.txt *.log $(REGRESS_RPT)
