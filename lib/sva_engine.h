// ==========================================================================
//  SVA + Functional Coverage Engine for Verilator
//  Provides NFA-based concurrent assertion evaluation and full covergroup
//  semantics via DPI-C, bridging the gap with commercial simulators.
// ==========================================================================
#ifndef SVA_ENGINE_H
#define SVA_ENGINE_H

#include <cstdint>
#include <string>
#include <vector>
#include <map>
#include <unordered_map>
#include <memory>
#include <functional>

namespace sva {

// =====================================================================
//  Expression AST  (used by both SVA conditions and coverpoint exprs)
// =====================================================================
enum class ExprOp {
    // Literals / leaves
    LITERAL,        // integer constant
    SIGNAL,         // signal name lookup
    // Unary
    NOT, BNOT, NEG,
    // Binary arithmetic
    ADD, SUB, MUL, DIV, MOD,
    // Shift
    SHL, SHR,
    // Relational
    LT, GT, LE, GE,
    // Equality
    EQ, NEQ,
    // Bitwise
    BAND, BXOR, BOR,
    // Logical
    AND, OR,
    // Ternary
    TERNARY,
    // SystemVerilog built-ins
    PAST,           // $past(signal)
    ROSE, FELL, STABLE,
};

struct ExprNode {
    ExprOp op;
    int64_t literal_val = 0;
    std::string signal_name;
    std::vector<std::unique_ptr<ExprNode>> children;  // 0-3 children

    ExprNode() : op(ExprOp::LITERAL) {}
    ExprNode(ExprOp o) : op(o) {}

    // Deep copy
    std::unique_ptr<ExprNode> clone() const;
};

// Parse a boolean/arithmetic expression string into an AST
std::unique_ptr<ExprNode> parse_expression(const std::string& expr);

// Evaluate an expression AST given a signal-value map
// prev_values is for $past/$rose/$fell/$stable
int64_t eval_expr(const ExprNode* node,
                  const std::unordered_map<std::string, int64_t>& values,
                  const std::unordered_map<std::string, int64_t>& prev_values);

// Extract all signal names referenced in an expression
void extract_signals(const ExprNode* node, std::vector<std::string>& out);

// =====================================================================
//  NFA for SVA Sequences
// =====================================================================
struct NfaEdge {
    int target;     // target node index
    int delay;      // 0 = epsilon (same cycle), 1+ = wait N cycles
};

struct NfaNode {
    int id;
    std::unique_ptr<ExprNode> condition;  // nullptr = always true (epsilon)
    bool is_accept = false;
    std::vector<NfaEdge> edges;
};

struct Nfa {
    std::vector<NfaNode> nodes;
    int start = 0;

    int add_node();
    int add_condition_node(std::unique_ptr<ExprNode> cond);
    int add_accept_node();
    void add_edge(int from, int to, int delay = 0);
};

// Parse an SVA sequence string into an NFA
Nfa parse_sequence(const std::string& seq_str);

// Parse a full SVA property string (with disable iff, implication)
struct Property {
    std::string disable_iff_expr;       // raw expression string
    std::unique_ptr<ExprNode> disable_cond;  // parsed disable condition

    Nfa antecedent;
    Nfa consequent;

    enum ImplType { NONE, OVERLAPPING, NON_OVERLAPPING };
    ImplType impl_type = NONE;
};

Property parse_property(const std::string& prop_str);

// =====================================================================
//  Runtime Assertion Evaluator (Token/Thread model)
// =====================================================================
struct Token {
    int node_id;
    int delay_remaining;
    uint64_t birth_cycle;
    uint32_t attempt_id;    // groups tokens from same trigger
};

class Assertion {
public:
    std::string name;
    std::string source_file;
    int source_line = 0;

    Property property;

    enum Kind { ASSERT, COVER, ASSUME };
    Kind kind = ASSERT;

    // Runtime state
    std::vector<Token> ante_tokens;
    std::vector<Token> cons_tokens;
    uint64_t pass_count = 0;
    uint64_t fail_count = 0;
    uint64_t vacuous_count = 0;
    uint64_t attempt_count = 0;
    std::vector<uint64_t> fail_times;  // first N failure sim times for report
    std::string fail_message;  // custom message from 'else' clause
    bool enabled = true;

    // Evaluate one clock tick with current signal values
    void tick(uint64_t cycle, uint64_t sim_time,
              const std::unordered_map<std::string, int64_t>& values,
              const std::unordered_map<std::string, int64_t>& prev_values);

private:
    uint32_t next_attempt_id_ = 0;
    void advance_tokens(std::vector<Token>& tokens, const Nfa& nfa,
                        const std::unordered_map<std::string, int64_t>& values,
                        const std::unordered_map<std::string, int64_t>& prev_values,
                        std::vector<uint32_t>& passed_attempts,
                        std::vector<uint32_t>& failed_attempts);
};

// =====================================================================
//  Functional Coverage Model
// =====================================================================
struct CovBin {
    std::string name;
    enum Type { VALUE_RANGE, TRANSITION, WILDCARD, ILLEGAL, IGNORE, DEFAULT, AUTO };
    Type type = VALUE_RANGE;

    // For VALUE_RANGE: list of [lo, hi] ranges
    std::vector<std::pair<int64_t, int64_t>> ranges;

    // For TRANSITION: sequence of values (state0 => state1 => ...)
    std::vector<std::vector<int64_t>> transitions;

    // For WILDCARD: mask and pattern (val & mask == pattern)
    uint64_t wc_mask = 0;
    uint64_t wc_pattern = 0;

    uint64_t hit_count = 0;

    bool match_value(int64_t val) const;
    // Returns true if the transition sequence ending with 'val' matches
    bool match_transition(const std::vector<int64_t>& history) const;
};

class Coverpoint {
public:
    std::string name;
    std::vector<CovBin> bins;
    int auto_bin_max = 64;      // option.auto_bin_max
    bool has_auto_bins = false;

    // Transition tracking
    std::vector<int64_t> value_history;
    static constexpr int MAX_HISTORY = 16;

    uint64_t sample_count = 0;
    int64_t last_value = 0;

    void sample(int64_t value);
    uint64_t bins_hit() const;
    uint64_t total_bins() const;  // excludes IGNORE/ILLEGAL from denominator
    double coverage_pct() const;
};

class CrossCoverage {
public:
    std::string name;
    std::vector<int> coverpoint_indices;  // indices into parent covergroup

    // Cross bin storage: key = tuple of bin indices, value = hit count
    std::map<std::vector<int>, uint64_t> cross_hits;

    uint64_t total_cross_bins = 0;
    uint64_t bins_hit() const;
    double coverage_pct() const;

    void record(const std::vector<int>& bin_indices);
};

class Covergroup {
public:
    std::string name;
    std::string inst_name;
    std::vector<Coverpoint> coverpoints;
    std::vector<CrossCoverage> crosses;
    uint64_t sample_count = 0;

    // Options
    int auto_bin_max = 64;
    bool per_instance = false;
    double goal = 100.0;

    int add_coverpoint(const std::string& cp_name);
    int add_cross(const std::string& x_name, const std::vector<int>& cp_ids);
    void sample();  // called after all coverpoint values are set
    double coverage_pct() const;
};

// =====================================================================
//  Global Engine (singleton)
// =====================================================================
class Engine {
public:
    static Engine& instance();

    // Signal management
    void set_signal(const std::string& name, int64_t value);
    int64_t get_signal(const std::string& name) const;

    // Assertion management
    int create_assertion(const std::string& name, const std::string& prop_expr,
                         int kind, const std::string& file = "", int line = 0);
    Assertion* get_assertion(int id);

    // Tick all assertions (sim_time is the current $time from SV)
    void tick(uint64_t sim_time);

    // Covergroup management
    int create_covergroup(const std::string& name);
    Covergroup* get_covergroup(int id);
    int cg_add_coverpoint(int cg_id, const std::string& cp_name);
    void cg_add_bin(int cp_id, const std::string& bin_name,
                    int64_t lo, int64_t hi);
    void cg_add_bin_list(int cp_id, const std::string& bin_name,
                         const std::vector<int64_t>& values);
    void cg_add_transition_bin(int cp_id, const std::string& bin_name,
                               const std::vector<int64_t>& sequence);
    void cg_add_wildcard_bin(int cp_id, const std::string& bin_name,
                             uint64_t mask, uint64_t pattern);
    void cg_add_illegal_bin(int cp_id, const std::string& bin_name,
                            int64_t lo, int64_t hi);
    void cg_add_ignore_bin(int cp_id, const std::string& bin_name,
                           int64_t lo, int64_t hi);
    void cg_add_default_bin(int cp_id, const std::string& bin_name);
    void cg_add_auto_bins(int cp_id, int count);
    void cg_sample_point(int cp_id, int64_t value);
    void cg_sample(int cg_id);

    // Cross (supports 2-way and N-way)
    int cg_add_cross(int cg_id, const std::string& name,
                     const std::vector<int>& cp_ids);

    // Reporting
    void report(const std::string& filename = "sva_report.txt");
    void reset();

    uint64_t cycle() const { return cycle_; }

    // Optional callback invoked on assertion failure (name, message)
    using FailCallback = void(*)(const char* name, const char* msg);
    void set_fail_callback(FailCallback cb) { fail_callback_ = cb; }

private:
    Engine() = default;
    FailCallback fail_callback_ = nullptr;

    uint64_t cycle_ = 0;
    std::unordered_map<std::string, int64_t> signals_;
    std::unordered_map<std::string, int64_t> prev_signals_;

    std::vector<std::unique_ptr<Assertion>> assertions_;
    std::vector<std::unique_ptr<Covergroup>> covergroups_;

    // Coverpoint global registry (flat, for DPI indexing)
    // Maps global cp_id -> (cg_index, cp_index_within_cg)
    struct CpRef { int cg_idx; int cp_idx; };
    std::vector<CpRef> cp_registry_;
};

}  // namespace sva

#endif  // SVA_ENGINE_H
