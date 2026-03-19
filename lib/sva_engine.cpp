// ==========================================================================
//  SVA + Functional Coverage Engine — Implementation
// ==========================================================================
#include "sva_engine.h"

#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <numeric>
#include <set>
#include <sstream>
#include <stdexcept>

namespace sva {

// =====================================================================
//  Expression Tokenizer
// =====================================================================
enum class TokKind {
    NUM, IDENT, STR,
    PLUS, MINUS, STAR, SLASH, PERCENT,
    EQ, NEQ, LT, GT, LE, GE,
    AND, OR, BAND, BOR, BXOR, BNOT, NOT,
    SHL, SHR,
    LPAREN, RPAREN, LBRACKET, RBRACKET,
    QUESTION, COLON, COMMA, DOT,
    // SVA-specific
    HASH_HASH,      // ##
    IMPL_OVER,      // |->
    IMPL_NON,       // |=>
    DOLLAR,         // $
    SEMI,
    END
};

struct Token_ {
    TokKind kind;
    std::string text;
    int64_t num_val = 0;
};

class Lexer {
public:
    explicit Lexer(const std::string& src) : src_(src), pos_(0) {}

    Token_ next() {
        skip_ws();
        if (pos_ >= src_.size()) return {TokKind::END, "", 0};

        char c = src_[pos_];

        // Two-character tokens
        if (pos_ + 1 < src_.size()) {
            char c2 = src_[pos_ + 1];
            if (c == '#' && c2 == '#') { pos_ += 2; return {TokKind::HASH_HASH, "##"}; }
            if (c == '|' && c2 == '-' && pos_ + 2 < src_.size() && src_[pos_ + 2] == '>') {
                pos_ += 3; return {TokKind::IMPL_OVER, "|->"};
            }
            if (c == '|' && c2 == '=' && pos_ + 2 < src_.size() && src_[pos_ + 2] == '>') {
                pos_ += 3; return {TokKind::IMPL_NON, "|=>"};
            }
            if (c == '=' && c2 == '=') { pos_ += 2; return {TokKind::EQ, "=="}; }
            if (c == '!' && c2 == '=') { pos_ += 2; return {TokKind::NEQ, "!="}; }
            if (c == '<' && c2 == '=') { pos_ += 2; return {TokKind::LE, "<="}; }
            if (c == '>' && c2 == '=') { pos_ += 2; return {TokKind::GE, ">="}; }
            if (c == '&' && c2 == '&') { pos_ += 2; return {TokKind::AND, "&&"}; }
            if (c == '|' && c2 == '|') { pos_ += 2; return {TokKind::OR, "||"}; }
            if (c == '<' && c2 == '<') { pos_ += 2; return {TokKind::SHL, "<<"}; }
            if (c == '>' && c2 == '>') { pos_ += 2; return {TokKind::SHR, ">>"}; }
        }

        // Single-character tokens
        pos_++;
        switch (c) {
            case '+': return {TokKind::PLUS, "+"};
            case '-': return {TokKind::MINUS, "-"};
            case '*': return {TokKind::STAR, "*"};
            case '/': return {TokKind::SLASH, "/"};
            case '%': return {TokKind::PERCENT, "%"};
            case '<': return {TokKind::LT, "<"};
            case '>': return {TokKind::GT, ">"};
            case '&': return {TokKind::BAND, "&"};
            case '|': return {TokKind::BOR, "|"};
            case '^': return {TokKind::BXOR, "^"};
            case '~': return {TokKind::BNOT, "~"};
            case '!': return {TokKind::NOT, "!"};
            case '(': return {TokKind::LPAREN, "("};
            case ')': return {TokKind::RPAREN, ")"};
            case '[': return {TokKind::LBRACKET, "["};
            case ']': return {TokKind::RBRACKET, "]"};
            case '?': return {TokKind::QUESTION, "?"};
            case ':': return {TokKind::COLON, ":"};
            case ',': return {TokKind::COMMA, ","};
            case '.': return {TokKind::DOT, "."};
            case '$': return {TokKind::DOLLAR, "$"};
            case ';': return {TokKind::SEMI, ";"};
            default: break;
        }
        pos_--;  // un-consume

        // Numbers: decimal, hex (32'hFF style or 0xFF), binary
        if (std::isdigit(c)) {
            return lex_number();
        }

        // Identifiers and keywords (including $past, $rose, etc.)
        if (c == '$' || std::isalpha(c) || c == '_') {
            return lex_ident();
        }

        // Skip unknown
        pos_++;
        return {TokKind::END, ""};
    }

    Token_ peek() {
        size_t saved = pos_;
        auto tok = next();
        pos_ = saved;
        return tok;
    }

    size_t pos() const { return pos_; }
    void set_pos(size_t p) { pos_ = p; }

private:
    void skip_ws() {
        while (pos_ < src_.size() && std::isspace(src_[pos_])) pos_++;
    }

    Token_ lex_number() {
        size_t start = pos_;
        // Check for SV-style literals: <width>'<base><digits>
        // First read decimal digits
        while (pos_ < src_.size() && std::isdigit(src_[pos_])) pos_++;

        if (pos_ < src_.size() && src_[pos_] == '\'') {
            // SV literal: width'base_digits
            pos_++;  // skip '
            if (pos_ < src_.size()) {
                char base = std::tolower(src_[pos_]);
                pos_++;
                int64_t val = 0;
                if (base == 'h') {
                    while (pos_ < src_.size() && std::isxdigit(src_[pos_])) {
                        char ch = std::tolower(src_[pos_++]);
                        val = val * 16 + (ch >= 'a' ? ch - 'a' + 10 : ch - '0');
                    }
                } else if (base == 'b') {
                    while (pos_ < src_.size() && (src_[pos_] == '0' || src_[pos_] == '1')) {
                        val = val * 2 + (src_[pos_++] - '0');
                    }
                } else if (base == 'o') {
                    while (pos_ < src_.size() && src_[pos_] >= '0' && src_[pos_] <= '7') {
                        val = val * 8 + (src_[pos_++] - '0');
                    }
                } else if (base == 'd') {
                    while (pos_ < src_.size() && std::isdigit(src_[pos_])) {
                        val = val * 10 + (src_[pos_++] - '0');
                    }
                }
                std::string txt = src_.substr(start, pos_ - start);
                return {TokKind::NUM, txt, val};
            }
        }

        // Plain decimal or 0x hex
        pos_ = start;
        if (pos_ + 1 < src_.size() && src_[pos_] == '0' &&
            std::tolower(src_[pos_ + 1]) == 'x') {
            pos_ += 2;
            int64_t val = 0;
            while (pos_ < src_.size() && std::isxdigit(src_[pos_])) {
                char ch = std::tolower(src_[pos_++]);
                val = val * 16 + (ch >= 'a' ? ch - 'a' + 10 : ch - '0');
            }
            return {TokKind::NUM, src_.substr(start, pos_ - start), val};
        }

        // Plain decimal
        int64_t val = 0;
        while (pos_ < src_.size() && std::isdigit(src_[pos_])) {
            val = val * 10 + (src_[pos_++] - '0');
        }
        return {TokKind::NUM, src_.substr(start, pos_ - start), val};
    }

    Token_ lex_ident() {
        size_t start = pos_;
        if (src_[pos_] == '$') pos_++;  // include $ prefix for builtins
        while (pos_ < src_.size() &&
               (std::isalnum(src_[pos_]) || src_[pos_] == '_')) {
            pos_++;
        }
        // Handle hierarchical names: a.b.c
        while (pos_ < src_.size() && src_[pos_] == '.' &&
               pos_ + 1 < src_.size() &&
               (std::isalpha(src_[pos_ + 1]) || src_[pos_ + 1] == '_')) {
            pos_++;  // skip dot
            while (pos_ < src_.size() &&
                   (std::isalnum(src_[pos_]) || src_[pos_] == '_')) {
                pos_++;
            }
        }
        std::string text = src_.substr(start, pos_ - start);
        return {TokKind::IDENT, text};
    }

    std::string src_;
    size_t pos_;
};

// =====================================================================
//  Expression Parser (recursive descent)
// =====================================================================
class ExprParser {
public:
    explicit ExprParser(Lexer& lex) : lex_(lex) { advance(); }

    std::unique_ptr<ExprNode> parse() { return parse_ternary(); }

    const Token_& current() const { return cur_; }

private:
    void advance() { cur_ = lex_.next(); }

    bool match(TokKind k) {
        if (cur_.kind == k) { advance(); return true; }
        return false;
    }

    void expect(TokKind k) {
        if (!match(k)) {
            throw std::runtime_error("Expected token kind " +
                                     std::to_string((int)k));
        }
    }

    std::unique_ptr<ExprNode> parse_ternary() {
        auto node = parse_logical_or();
        if (cur_.kind == TokKind::QUESTION) {
            advance();
            auto t = std::make_unique<ExprNode>(ExprOp::TERNARY);
            auto then_expr = parse_ternary();
            expect(TokKind::COLON);
            auto else_expr = parse_ternary();
            t->children.push_back(std::move(node));
            t->children.push_back(std::move(then_expr));
            t->children.push_back(std::move(else_expr));
            return t;
        }
        return node;
    }

    std::unique_ptr<ExprNode> parse_logical_or() {
        auto left = parse_logical_and();
        while (cur_.kind == TokKind::OR) {
            advance();
            auto right = parse_logical_and();
            auto n = std::make_unique<ExprNode>(ExprOp::OR);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_logical_and() {
        auto left = parse_bitwise_or();
        while (cur_.kind == TokKind::AND) {
            advance();
            auto right = parse_bitwise_or();
            auto n = std::make_unique<ExprNode>(ExprOp::AND);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_bitwise_or() {
        auto left = parse_bitwise_xor();
        while (cur_.kind == TokKind::BOR) {
            advance();
            auto right = parse_bitwise_xor();
            auto n = std::make_unique<ExprNode>(ExprOp::BOR);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_bitwise_xor() {
        auto left = parse_bitwise_and();
        while (cur_.kind == TokKind::BXOR) {
            advance();
            auto right = parse_bitwise_and();
            auto n = std::make_unique<ExprNode>(ExprOp::BXOR);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_bitwise_and() {
        auto left = parse_equality();
        while (cur_.kind == TokKind::BAND) {
            advance();
            auto right = parse_equality();
            auto n = std::make_unique<ExprNode>(ExprOp::BAND);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_equality() {
        auto left = parse_relational();
        while (cur_.kind == TokKind::EQ || cur_.kind == TokKind::NEQ) {
            ExprOp op = (cur_.kind == TokKind::EQ) ? ExprOp::EQ : ExprOp::NEQ;
            advance();
            auto right = parse_relational();
            auto n = std::make_unique<ExprNode>(op);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_relational() {
        auto left = parse_shift();
        while (cur_.kind == TokKind::LT || cur_.kind == TokKind::GT ||
               cur_.kind == TokKind::LE || cur_.kind == TokKind::GE) {
            ExprOp op;
            switch (cur_.kind) {
                case TokKind::LT: op = ExprOp::LT; break;
                case TokKind::GT: op = ExprOp::GT; break;
                case TokKind::LE: op = ExprOp::LE; break;
                default:          op = ExprOp::GE; break;
            }
            advance();
            auto right = parse_shift();
            auto n = std::make_unique<ExprNode>(op);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_shift() {
        auto left = parse_additive();
        while (cur_.kind == TokKind::SHL || cur_.kind == TokKind::SHR) {
            ExprOp op = (cur_.kind == TokKind::SHL) ? ExprOp::SHL : ExprOp::SHR;
            advance();
            auto right = parse_additive();
            auto n = std::make_unique<ExprNode>(op);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_additive() {
        auto left = parse_multiplicative();
        while (cur_.kind == TokKind::PLUS || cur_.kind == TokKind::MINUS) {
            ExprOp op = (cur_.kind == TokKind::PLUS) ? ExprOp::ADD : ExprOp::SUB;
            advance();
            auto right = parse_multiplicative();
            auto n = std::make_unique<ExprNode>(op);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_multiplicative() {
        auto left = parse_unary();
        while (cur_.kind == TokKind::STAR || cur_.kind == TokKind::SLASH ||
               cur_.kind == TokKind::PERCENT) {
            ExprOp op;
            switch (cur_.kind) {
                case TokKind::STAR:    op = ExprOp::MUL; break;
                case TokKind::SLASH:   op = ExprOp::DIV; break;
                default:               op = ExprOp::MOD; break;
            }
            advance();
            auto right = parse_unary();
            auto n = std::make_unique<ExprNode>(op);
            n->children.push_back(std::move(left));
            n->children.push_back(std::move(right));
            left = std::move(n);
        }
        return left;
    }

    std::unique_ptr<ExprNode> parse_unary() {
        if (cur_.kind == TokKind::NOT) {
            advance();
            auto n = std::make_unique<ExprNode>(ExprOp::NOT);
            n->children.push_back(parse_unary());
            return n;
        }
        if (cur_.kind == TokKind::BNOT) {
            advance();
            auto n = std::make_unique<ExprNode>(ExprOp::BNOT);
            n->children.push_back(parse_unary());
            return n;
        }
        if (cur_.kind == TokKind::MINUS) {
            advance();
            auto n = std::make_unique<ExprNode>(ExprOp::NEG);
            n->children.push_back(parse_unary());
            return n;
        }
        return parse_primary();
    }

    std::unique_ptr<ExprNode> parse_primary() {
        if (cur_.kind == TokKind::NUM) {
            auto n = std::make_unique<ExprNode>(ExprOp::LITERAL);
            n->literal_val = cur_.num_val;
            advance();
            return n;
        }

        if (cur_.kind == TokKind::LPAREN) {
            advance();
            auto n = parse_ternary();
            expect(TokKind::RPAREN);
            return n;
        }

        if (cur_.kind == TokKind::IDENT) {
            std::string name = cur_.text;
            advance();

            // Handle built-in functions: $past, $rose, $fell, $stable
            if (name == "$past") {
                expect(TokKind::LPAREN);
                auto arg = parse_ternary();
                expect(TokKind::RPAREN);
                auto n = std::make_unique<ExprNode>(ExprOp::PAST);
                n->children.push_back(std::move(arg));
                return n;
            }
            if (name == "$rose") {
                expect(TokKind::LPAREN);
                auto arg = parse_ternary();
                expect(TokKind::RPAREN);
                auto n = std::make_unique<ExprNode>(ExprOp::ROSE);
                n->children.push_back(std::move(arg));
                return n;
            }
            if (name == "$fell") {
                expect(TokKind::LPAREN);
                auto arg = parse_ternary();
                expect(TokKind::RPAREN);
                auto n = std::make_unique<ExprNode>(ExprOp::FELL);
                n->children.push_back(std::move(arg));
                return n;
            }
            if (name == "$stable") {
                expect(TokKind::LPAREN);
                auto arg = parse_ternary();
                expect(TokKind::RPAREN);
                auto n = std::make_unique<ExprNode>(ExprOp::STABLE);
                n->children.push_back(std::move(arg));
                return n;
            }

            // Handle bit-select: signal[N] or signal[M:N]
            // Treat as a compound signal name
            if (cur_.kind == TokKind::LBRACKET) {
                // Save position, try to parse bit select
                // For simplicity, fold into signal name
                std::string full = name + "[";
                advance();  // skip [
                while (cur_.kind != TokKind::RBRACKET && cur_.kind != TokKind::END) {
                    full += cur_.text;
                    advance();
                }
                if (cur_.kind == TokKind::RBRACKET) {
                    full += "]";
                    advance();
                }
                auto n = std::make_unique<ExprNode>(ExprOp::SIGNAL);
                n->signal_name = full;
                return n;
            }

            // Regular signal reference
            auto n = std::make_unique<ExprNode>(ExprOp::SIGNAL);
            n->signal_name = name;
            return n;
        }

        // Fallback: treat as 0
        auto n = std::make_unique<ExprNode>(ExprOp::LITERAL);
        n->literal_val = 0;
        if (cur_.kind != TokKind::END) advance();
        return n;
    }

    Lexer& lex_;
    Token_ cur_;
};

// =====================================================================
//  Expression AST utilities
// =====================================================================
std::unique_ptr<ExprNode> ExprNode::clone() const {
    auto c = std::make_unique<ExprNode>(op);
    c->literal_val = literal_val;
    c->signal_name = signal_name;
    for (auto& ch : children) {
        c->children.push_back(ch->clone());
    }
    return c;
}

std::unique_ptr<ExprNode> parse_expression(const std::string& expr) {
    Lexer lex(expr);
    ExprParser parser(lex);
    return parser.parse();
}

int64_t eval_expr(const ExprNode* node,
                  const std::unordered_map<std::string, int64_t>& values,
                  const std::unordered_map<std::string, int64_t>& prev_values) {
    if (!node) return 0;

    switch (node->op) {
        case ExprOp::LITERAL:
            return node->literal_val;

        case ExprOp::SIGNAL: {
            auto it = values.find(node->signal_name);
            return (it != values.end()) ? it->second : 0;
        }

        case ExprOp::NOT:
            return eval_expr(node->children[0].get(), values, prev_values) ? 0 : 1;
        case ExprOp::BNOT:
            return ~eval_expr(node->children[0].get(), values, prev_values);
        case ExprOp::NEG:
            return -eval_expr(node->children[0].get(), values, prev_values);

        case ExprOp::ADD:
            return eval_expr(node->children[0].get(), values, prev_values) +
                   eval_expr(node->children[1].get(), values, prev_values);
        case ExprOp::SUB:
            return eval_expr(node->children[0].get(), values, prev_values) -
                   eval_expr(node->children[1].get(), values, prev_values);
        case ExprOp::MUL:
            return eval_expr(node->children[0].get(), values, prev_values) *
                   eval_expr(node->children[1].get(), values, prev_values);
        case ExprOp::DIV: {
            auto d = eval_expr(node->children[1].get(), values, prev_values);
            return d ? eval_expr(node->children[0].get(), values, prev_values) / d : 0;
        }
        case ExprOp::MOD: {
            auto d = eval_expr(node->children[1].get(), values, prev_values);
            return d ? eval_expr(node->children[0].get(), values, prev_values) % d : 0;
        }

        case ExprOp::SHL:
            return eval_expr(node->children[0].get(), values, prev_values) <<
                   eval_expr(node->children[1].get(), values, prev_values);
        case ExprOp::SHR:
            return (uint64_t)eval_expr(node->children[0].get(), values, prev_values) >>
                   eval_expr(node->children[1].get(), values, prev_values);

        case ExprOp::LT:
            return eval_expr(node->children[0].get(), values, prev_values) <
                   eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0;
        case ExprOp::GT:
            return eval_expr(node->children[0].get(), values, prev_values) >
                   eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0;
        case ExprOp::LE:
            return eval_expr(node->children[0].get(), values, prev_values) <=
                   eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0;
        case ExprOp::GE:
            return eval_expr(node->children[0].get(), values, prev_values) >=
                   eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0;

        case ExprOp::EQ:
            return eval_expr(node->children[0].get(), values, prev_values) ==
                   eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0;
        case ExprOp::NEQ:
            return eval_expr(node->children[0].get(), values, prev_values) !=
                   eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0;

        case ExprOp::BAND:
            return eval_expr(node->children[0].get(), values, prev_values) &
                   eval_expr(node->children[1].get(), values, prev_values);
        case ExprOp::BXOR:
            return eval_expr(node->children[0].get(), values, prev_values) ^
                   eval_expr(node->children[1].get(), values, prev_values);
        case ExprOp::BOR:
            return eval_expr(node->children[0].get(), values, prev_values) |
                   eval_expr(node->children[1].get(), values, prev_values);

        case ExprOp::AND: {
            auto l = eval_expr(node->children[0].get(), values, prev_values);
            return l ? (eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0) : 0;
        }
        case ExprOp::OR: {
            auto l = eval_expr(node->children[0].get(), values, prev_values);
            return l ? 1 : (eval_expr(node->children[1].get(), values, prev_values) ? 1 : 0);
        }

        case ExprOp::TERNARY: {
            auto c = eval_expr(node->children[0].get(), values, prev_values);
            return c ? eval_expr(node->children[1].get(), values, prev_values)
                     : eval_expr(node->children[2].get(), values, prev_values);
        }

        case ExprOp::PAST: {
            // Evaluate child with previous values
            return eval_expr(node->children[0].get(), prev_values, prev_values);
        }
        case ExprOp::ROSE: {
            auto cur = eval_expr(node->children[0].get(), values, prev_values);
            auto prev = eval_expr(node->children[0].get(), prev_values, prev_values);
            return (!prev && cur) ? 1 : 0;
        }
        case ExprOp::FELL: {
            auto cur = eval_expr(node->children[0].get(), values, prev_values);
            auto prev = eval_expr(node->children[0].get(), prev_values, prev_values);
            return (prev && !cur) ? 1 : 0;
        }
        case ExprOp::STABLE: {
            auto cur = eval_expr(node->children[0].get(), values, prev_values);
            auto prev = eval_expr(node->children[0].get(), prev_values, prev_values);
            return (cur == prev) ? 1 : 0;
        }
    }
    return 0;
}

void extract_signals(const ExprNode* node, std::vector<std::string>& out) {
    if (!node) return;
    if (node->op == ExprOp::SIGNAL) {
        // Don't add duplicates
        if (std::find(out.begin(), out.end(), node->signal_name) == out.end()) {
            out.push_back(node->signal_name);
        }
    }
    for (auto& ch : node->children) {
        extract_signals(ch.get(), out);
    }
}

// =====================================================================
//  NFA construction
// =====================================================================
int Nfa::add_node() {
    int id = (int)nodes.size();
    nodes.emplace_back();
    nodes.back().id = id;
    return id;
}

int Nfa::add_condition_node(std::unique_ptr<ExprNode> cond) {
    int id = add_node();
    nodes[id].condition = std::move(cond);
    return id;
}

int Nfa::add_accept_node() {
    int id = add_node();
    nodes[id].is_accept = true;
    return id;
}

void Nfa::add_edge(int from, int to, int delay) {
    nodes[from].edges.push_back({to, delay});
}

// =====================================================================
//  SVA Sequence/Property Parser
// =====================================================================
// This parser handles:
//   - Boolean expressions as atomic sequences
//   - ## delays: ##N, ##[M:N], ##[M:$]
//   - |-> and |=> implications
//   - disable iff (expr)
//   - [*N], [*M:N] consecutive repetition
//   - [->N] goto repetition
//   - [=N] non-consecutive repetition
//   - first_match(seq)
//   - Sequence 'or' and 'and' operators

class SvaParser {
public:
    explicit SvaParser(const std::string& src) : lex_(src) { advance(); }

    Property parse_property() {
        Property prop;

        // Check for 'disable iff (expr)'
        if (cur_.kind == TokKind::IDENT && cur_.text == "disable") {
            advance();
            // expect 'iff'
            if (cur_.kind == TokKind::IDENT && cur_.text == "iff") {
                advance();
            }
            expect(TokKind::LPAREN);
            // Parse the disable expression — collect tokens until matching ')'
            std::string disable_expr;
            int depth = 1;
            while (depth > 0 && cur_.kind != TokKind::END) {
                if (cur_.kind == TokKind::LPAREN) depth++;
                if (cur_.kind == TokKind::RPAREN) {
                    depth--;
                    if (depth == 0) break;
                }
                disable_expr += cur_.text + " ";
                advance();
            }
            expect(TokKind::RPAREN);
            prop.disable_iff_expr = disable_expr;
            prop.disable_cond = parse_expression(disable_expr);
        }

        // Now parse the property body.
        // We need to look ahead for |-> or |=> to decide if there's an implication.
        // Strategy: parse a sequence, then check for implication.
        Nfa seq = parse_sequence_expr();

        if (cur_.kind == TokKind::IMPL_OVER || cur_.kind == TokKind::IMPL_NON) {
            prop.impl_type = (cur_.kind == TokKind::IMPL_OVER)
                ? Property::OVERLAPPING : Property::NON_OVERLAPPING;
            advance();
            prop.antecedent = std::move(seq);
            prop.consequent = parse_sequence_expr();
        } else {
            // Simple sequence (no implication) — treat entire thing as consequent
            prop.impl_type = Property::NONE;
            prop.consequent = std::move(seq);
        }

        return prop;
    }

private:
    void advance() { cur_ = lex_.next(); }

    bool match(TokKind k) {
        if (cur_.kind == k) { advance(); return true; }
        return false;
    }

    void expect(TokKind k) {
        if (!match(k)) {
            // Soft error — continue
        }
    }

    // Parse a sequence expression (handles 'or' at lowest precedence)
    Nfa parse_sequence_expr() {
        auto left = parse_sequence_and();

        while (cur_.kind == TokKind::IDENT && cur_.text == "or") {
            advance();
            auto right = parse_sequence_and();
            // Merge: new start with epsilon edges to both starts, both accepts
            Nfa merged;
            int s = merged.add_node();
            merged.start = s;
            // Copy left NFA
            int left_offset = (int)merged.nodes.size();
            for (auto& n : left.nodes) {
                int id = merged.add_node();
                merged.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                merged.nodes[id].is_accept = n.is_accept;
                for (auto& e : n.edges) {
                    merged.nodes[id].edges.push_back({e.target + left_offset, e.delay});
                }
            }
            merged.add_edge(s, left.start + left_offset, 0);

            // Copy right NFA
            int right_offset = (int)merged.nodes.size();
            for (auto& n : right.nodes) {
                int id = merged.add_node();
                merged.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                merged.nodes[id].is_accept = n.is_accept;
                for (auto& e : n.edges) {
                    merged.nodes[id].edges.push_back({e.target + right_offset, e.delay});
                }
            }
            merged.add_edge(s, right.start + right_offset, 0);

            left = std::move(merged);
        }

        return left;
    }

    // Parse sequence 'and'
    Nfa parse_sequence_and() {
        auto left = parse_sequence_concat();

        while (cur_.kind == TokKind::IDENT &&
               (cur_.text == "and" || cur_.text == "intersect")) {
            // For 'and': both must match (possibly different lengths)
            // For 'intersect': both must match with same length
            // Simplified implementation: treat as concurrent match (both must succeed)
            // In the token model, we track tokens for both and require both to reach accept
            advance();
            auto right = parse_sequence_concat();
            // For simplicity, concatenate: left then right (approximate)
            // A full implementation would track parallel threads
            // TODO: proper parallel thread tracking for 'and'/'intersect'
            Nfa combined;
            int s = combined.add_node();
            combined.start = s;
            // Copy left
            int lo = (int)combined.nodes.size();
            for (auto& n : left.nodes) {
                int id = combined.add_node();
                combined.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                for (auto& e : n.edges) {
                    combined.nodes[id].edges.push_back({e.target + lo, e.delay});
                }
            }
            combined.add_edge(s, left.start + lo, 0);
            // Find accept nodes of left, add edges to right
            int ro = (int)combined.nodes.size();
            for (auto& n : right.nodes) {
                int id = combined.add_node();
                combined.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                combined.nodes[id].is_accept = n.is_accept;
                for (auto& e : n.edges) {
                    combined.nodes[id].edges.push_back({e.target + ro, e.delay});
                }
            }
            for (int i = lo; i < ro; i++) {
                if (left.nodes[i - lo].is_accept) {
                    combined.add_edge(i, right.start + ro, 0);
                }
            }
            left = std::move(combined);
        }

        return left;
    }

    // Parse concatenation with ## delays
    Nfa parse_sequence_concat() {
        auto left = parse_sequence_repetition();

        while (cur_.kind == TokKind::HASH_HASH) {
            advance();

            int delay_min = 0, delay_max = 0;
            parse_delay(delay_min, delay_max);

            auto right = parse_sequence_repetition();

            // Build concatenated NFA: left --##[min:max]--> right
            Nfa combined;
            combined.start = 0;

            // Copy left NFA
            for (auto& n : left.nodes) {
                int id = combined.add_node();
                combined.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                for (auto& e : n.edges) {
                    combined.nodes[id].edges.push_back({e.target, e.delay});
                }
            }

            // Copy right NFA with offset
            int right_offset = (int)combined.nodes.size();
            for (auto& n : right.nodes) {
                int id = combined.add_node();
                combined.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                combined.nodes[id].is_accept = n.is_accept;
                for (auto& e : n.edges) {
                    combined.nodes[id].edges.push_back({e.target + right_offset, e.delay});
                }
            }

            // Connect left's accept nodes to right's start with delay
            for (int i = 0; i < right_offset; i++) {
                if (left.nodes[i].is_accept) {
                    for (int d = delay_min; d <= delay_max; d++) {
                        combined.add_edge(i, right.start + right_offset, d);
                    }
                    // Remove accept status from left's nodes in combined
                    combined.nodes[i].is_accept = false;
                }
            }

            left = std::move(combined);
        }

        return left;
    }

    void parse_delay(int& dmin, int& dmax) {
        if (cur_.kind == TokKind::LBRACKET) {
            advance();
            dmin = 0;
            if (cur_.kind == TokKind::NUM) {
                dmin = (int)cur_.num_val;
                advance();
            }
            if (cur_.kind == TokKind::COLON) {
                advance();
                if (cur_.kind == TokKind::DOLLAR) {
                    dmax = 1000;  // practical unbounded limit
                    advance();
                } else if (cur_.kind == TokKind::NUM) {
                    dmax = (int)cur_.num_val;
                    advance();
                } else {
                    dmax = dmin;
                }
            } else {
                dmax = dmin;
            }
            expect(TokKind::RBRACKET);
        } else if (cur_.kind == TokKind::NUM) {
            dmin = dmax = (int)cur_.num_val;
            advance();
        } else {
            dmin = dmax = 1;  // default ##1
        }
    }

    // Parse repetition: base[*N], base[*M:N], base[->N], base[=N]
    Nfa parse_sequence_repetition() {
        auto base = parse_sequence_primary();

        while (cur_.kind == TokKind::LBRACKET) {
            // Peek for *, ->, =
            size_t saved = lex_.pos();
            advance();  // skip [

            if (cur_.kind == TokKind::STAR) {
                // [*N] or [*M:N] — consecutive repetition
                advance();
                int rmin = 0, rmax = 0;
                if (cur_.kind == TokKind::NUM) {
                    rmin = (int)cur_.num_val;
                    advance();
                }
                if (cur_.kind == TokKind::COLON) {
                    advance();
                    if (cur_.kind == TokKind::DOLLAR) {
                        rmax = 1000;
                        advance();
                    } else if (cur_.kind == TokKind::NUM) {
                        rmax = (int)cur_.num_val;
                        advance();
                    }
                } else {
                    rmax = rmin;
                }
                if (rmin == 0 && rmax == 0) {
                    // [*] unbounded
                    rmax = 1000;
                }
                expect(TokKind::RBRACKET);
                base = build_repetition(std::move(base), rmin, rmax);

            } else if (cur_.kind == TokKind::MINUS &&
                       lex_.peek().kind == TokKind::GT) {
                // [->N] — goto repetition
                advance();  // skip -
                advance();  // skip >
                int count = 1;
                if (cur_.kind == TokKind::NUM) {
                    count = (int)cur_.num_val;
                    advance();
                }
                expect(TokKind::RBRACKET);
                base = build_goto_repetition(std::move(base), count);

            } else if (cur_.kind == TokKind::EQ ||
                       (cur_.kind != TokKind::NUM && cur_.kind != TokKind::RBRACKET)) {
                // [=N] — non-consecutive repetition
                if (cur_.kind == TokKind::EQ) advance();
                int count = 1;
                if (cur_.kind == TokKind::NUM) {
                    count = (int)cur_.num_val;
                    advance();
                }
                expect(TokKind::RBRACKET);
                base = build_nonconsec_repetition(std::move(base), count);
            } else {
                // Not a repetition bracket — restore and break
                lex_.set_pos(saved);
                cur_ = lex_.next();  // re-fetch the [ but actually we want to rewind
                // Actually let's just break
                break;
            }
        }

        return base;
    }

    Nfa build_repetition(Nfa base, int rmin, int rmax) {
        // Consecutive repetition: chain base rmin..rmax times
        // For efficiency, build an NFA that matches base repeated rmin to rmax times
        if (rmax < rmin) rmax = rmin;

        Nfa result;
        int start = result.add_node();
        result.start = start;

        if (rmin == 0) {
            // Can match zero times — start is also an accept
            result.nodes[start].is_accept = true;
        }

        int prev_accept = start;
        for (int i = 0; i < rmax; i++) {
            // Copy base NFA
            int offset = (int)result.nodes.size();
            for (auto& n : base.nodes) {
                int id = result.add_node();
                result.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                for (auto& e : n.edges) {
                    result.nodes[id].edges.push_back({e.target + offset, e.delay});
                }
                if (n.is_accept && i + 1 >= rmin) {
                    result.nodes[id].is_accept = true;
                }
            }
            // Connect previous end to this copy's start
            if (i == 0) {
                result.add_edge(start, base.start + offset, 0);
            } else {
                // Connect all accept nodes of previous copy to this copy's start
                for (int j = 0; j < offset; j++) {
                    if (result.nodes[j].is_accept &&
                        j != start) {  // don't loop from optional-zero accept
                        // For consecutive: next cycle (delay 1)
                        result.add_edge(j, base.start + offset, 1);
                        if (i >= rmin) {
                            // Keep as accept for optional repeats
                        } else {
                            result.nodes[j].is_accept = false;
                        }
                    }
                }
            }
        }

        return result;
    }

    Nfa build_goto_repetition(Nfa base, int count) {
        // [->N]: match base exactly N times, with arbitrary gaps between
        // The gap is modeled by: at each cycle where base doesn't match, stay put
        Nfa result;
        result.start = 0;

        int prev_start = result.add_node();  // start node

        for (int i = 0; i < count; i++) {
            // Add a waiting node (loops on non-match)
            int wait = result.add_node();
            result.add_edge(prev_start, wait, (i == 0) ? 0 : 1);

            // Self-loop: wait any number of cycles
            result.add_edge(wait, wait, 1);

            // Copy base for match
            int offset = (int)result.nodes.size();
            for (auto& n : base.nodes) {
                int id = result.add_node();
                result.nodes[id].condition = n.condition ? n.condition->clone() : nullptr;
                for (auto& e : n.edges) {
                    result.nodes[id].edges.push_back({e.target + offset, e.delay});
                }
                if (n.is_accept && i == count - 1) {
                    result.nodes[id].is_accept = true;  // final match
                }
            }
            result.add_edge(wait, base.start + offset, 0);
            prev_start = offset + base.start;  // simplified
        }

        return result;
    }

    Nfa build_nonconsec_repetition(Nfa base, int count) {
        // [=N]: same as [->N] but doesn't require the last match to be at the end
        // After the Nth match, more non-matching cycles are allowed
        Nfa nfa = build_goto_repetition(std::move(base), count);
        // Add self-loop on accept nodes to allow trailing non-matches
        for (auto& n : nfa.nodes) {
            if (n.is_accept) {
                nfa.add_edge(n.id, n.id, 1);
            }
        }
        return nfa;
    }

    Nfa parse_sequence_primary() {
        // Handle 'first_match(seq)'
        if (cur_.kind == TokKind::IDENT && cur_.text == "first_match") {
            advance();
            expect(TokKind::LPAREN);
            auto seq = parse_sequence_expr();
            expect(TokKind::RPAREN);
            // Mark this NFA for first_match behavior (in evaluation)
            // For now, structurally same — first_match is handled at runtime
            return seq;
        }

        // Handle parenthesized sub-sequence
        if (cur_.kind == TokKind::LPAREN) {
            advance();
            auto seq = parse_sequence_expr();
            expect(TokKind::RPAREN);
            return seq;
        }

        // Atomic: boolean expression
        // We need to parse an expression but stop before SVA operators
        // (##, |->, |=>, [, 'or', 'and', 'intersect', ')', ';', END)
        return parse_bool_expr_as_nfa();
    }

    Nfa parse_bool_expr_as_nfa() {
        // Collect tokens that form a boolean expression
        // Stop when we see SVA structural tokens
        std::string expr_str;
        int paren_depth = 0;

        while (cur_.kind != TokKind::END) {
            // Stop conditions (only at paren depth 0)
            if (paren_depth == 0) {
                if (cur_.kind == TokKind::HASH_HASH ||
                    cur_.kind == TokKind::IMPL_OVER ||
                    cur_.kind == TokKind::IMPL_NON ||
                    cur_.kind == TokKind::SEMI ||
                    cur_.kind == TokKind::RPAREN) break;

                if (cur_.kind == TokKind::LBRACKET) {
                    // Could be repetition — stop
                    break;
                }

                if (cur_.kind == TokKind::IDENT &&
                    (cur_.text == "or" || cur_.text == "and" ||
                     cur_.text == "intersect" || cur_.text == "within" ||
                     cur_.text == "throughout" || cur_.text == "disable" ||
                     cur_.text == "iff")) {
                    break;
                }
            }

            if (cur_.kind == TokKind::LPAREN) paren_depth++;
            if (cur_.kind == TokKind::RPAREN) paren_depth--;

            expr_str += cur_.text + " ";
            advance();
        }

        if (expr_str.empty()) {
            // Empty expression — always true
            Nfa nfa;
            nfa.start = nfa.add_accept_node();
            return nfa;
        }

        // Parse expression and create single-node NFA
        auto expr = parse_expression(expr_str);
        Nfa nfa;
        int node = nfa.add_condition_node(std::move(expr));
        nfa.start = node;
        int acc = nfa.add_accept_node();
        nfa.add_edge(node, acc, 0);

        return nfa;
    }

    Lexer lex_;
    Token_ cur_;
};

Nfa parse_sequence(const std::string& seq_str) {
    SvaParser parser(seq_str);
    // Parse as a concatenated sequence
    Property prop = parser.parse_property();
    return std::move(prop.consequent);
}

Property parse_property(const std::string& prop_str) {
    SvaParser parser(prop_str);
    return parser.parse_property();
}

// =====================================================================
//  Assertion Tick Evaluation
// =====================================================================
void Assertion::advance_tokens(
    std::vector<Token>& tokens,
    const Nfa& nfa,
    const std::unordered_map<std::string, int64_t>& values,
    const std::unordered_map<std::string, int64_t>& prev_values,
    std::vector<uint32_t>& passed_attempts,
    std::vector<uint32_t>& failed_attempts)
{
    std::vector<Token> next_tokens;

    for (auto& tok : tokens) {
        // --- Delay handling ---
        // ##N means "evaluate N cycles later".  When we decrement to 0
        // the token is ready and must be evaluated THIS tick, not next.
        if (tok.delay_remaining > 0) {
            tok.delay_remaining--;
            if (tok.delay_remaining > 0) {
                next_tokens.push_back(tok);
                continue;
            }
            // delay just hit 0 — fall through to evaluate NOW
        }

        auto& node = nfa.nodes[tok.node_id];

        // Evaluate condition (nullptr = always true)
        bool cond_pass = true;
        if (node.condition) {
            cond_pass = eval_expr(node.condition.get(), values, prev_values) != 0;
        }

        if (!cond_pass) {
            // Token fails — record as failed attempt
            failed_attempts.push_back(tok.attempt_id);
            continue;
        }

        // Condition passed
        if (node.is_accept) {
            passed_attempts.push_back(tok.attempt_id);
            continue;
        }

        // Advance to successor nodes
        for (auto& edge : node.edges) {
            if (edge.target >= 0 && edge.target < (int)nfa.nodes.size()) {
                Token new_tok;
                new_tok.node_id = edge.target;
                new_tok.delay_remaining = edge.delay;
                new_tok.birth_cycle = tok.birth_cycle;
                new_tok.attempt_id = tok.attempt_id;

                // If delay is 0 (epsilon), evaluate target immediately
                if (edge.delay == 0) {
                    auto& tgt = nfa.nodes[edge.target];
                    if (tgt.is_accept && !tgt.condition) {
                        passed_attempts.push_back(new_tok.attempt_id);
                        continue;
                    }
                    if (tgt.condition) {
                        bool tgt_pass = eval_expr(tgt.condition.get(), values, prev_values) != 0;
                        if (!tgt_pass) {
                            failed_attempts.push_back(new_tok.attempt_id);
                            continue;
                        }
                        if (tgt.is_accept) {
                            passed_attempts.push_back(new_tok.attempt_id);
                            continue;
                        }
                        // Continue from target's successors
                        for (auto& e2 : tgt.edges) {
                            Token t2;
                            t2.node_id = e2.target;
                            t2.delay_remaining = e2.delay;
                            t2.birth_cycle = tok.birth_cycle;
                            t2.attempt_id = tok.attempt_id;
                            next_tokens.push_back(t2);
                        }
                        continue;
                    }
                    // No condition, not accept: pass-through node
                    // (e.g. from parse_bool_expr_as_nfa's extra accept node
                    // that lost its accept status during sequence concat).
                    // Traverse its edges immediately without burning a tick.
                    if (!tgt.condition && !tgt.is_accept) {
                        for (auto& e2 : tgt.edges) {
                            Token t2;
                            t2.node_id = e2.target;
                            t2.delay_remaining = e2.delay;
                            t2.birth_cycle = tok.birth_cycle;
                            t2.attempt_id = tok.attempt_id;
                            next_tokens.push_back(t2);
                        }
                        continue;
                    }
                }
                next_tokens.push_back(new_tok);
            }
        }
    }

    tokens = std::move(next_tokens);
}

void Assertion::tick(
    uint64_t cycle, uint64_t sim_time,
    const std::unordered_map<std::string, int64_t>& values,
    const std::unordered_map<std::string, int64_t>& prev_values)
{
    if (!enabled) return;

    // Check disable condition
    if (property.disable_cond) {
        if (eval_expr(property.disable_cond.get(), values, prev_values) != 0) {
            // Assertion disabled this cycle — flush active tokens
            ante_tokens.clear();
            cons_tokens.clear();
            return;
        }
    }

    std::vector<uint32_t> passed_attempts, failed_attempts;

    if (property.impl_type == Property::NONE) {
        // Simple property (no implication)
        // Spawn a new token at the start of the consequent each cycle
        Token tok;
        tok.node_id = property.consequent.start;
        tok.delay_remaining = 0;
        tok.birth_cycle = cycle;
        tok.attempt_id = next_attempt_id_++;
        cons_tokens.push_back(tok);
        attempt_count++;

        advance_tokens(cons_tokens, property.consequent, values, prev_values,
                       passed_attempts, failed_attempts);

    } else {
        // Implication: first evaluate antecedent
        // Spawn a new antecedent token each cycle
        Token ante_tok;
        ante_tok.node_id = property.antecedent.start;
        ante_tok.delay_remaining = 0;
        ante_tok.birth_cycle = cycle;
        ante_tok.attempt_id = next_attempt_id_++;
        ante_tokens.push_back(ante_tok);

        std::vector<uint32_t> ante_passed, ante_failed;
        advance_tokens(ante_tokens, property.antecedent, values, prev_values,
                       ante_passed, ante_failed);

        // For each antecedent that passed, spawn consequent token
        for (auto aid : ante_passed) {
            Token cons_tok;
            cons_tok.node_id = property.consequent.start;
            cons_tok.delay_remaining =
                (property.impl_type == Property::NON_OVERLAPPING) ? 1 : 0;
            cons_tok.birth_cycle = cycle;
            cons_tok.attempt_id = aid;
            cons_tokens.push_back(cons_tok);
            attempt_count++;
        }

        // Count antecedent failures as vacuous passes
        // (antecedent didn't match, so implication vacuously true)
        // Only count if the token just started this cycle (single-cycle ante check)
        for (auto aid : ante_failed) {
            vacuous_count++;
        }

        // Advance consequent tokens
        advance_tokens(cons_tokens, property.consequent, values, prev_values,
                       passed_attempts, failed_attempts);
    }

    // Process results
    // De-duplicate by attempt_id
    std::set<uint32_t> passed_set(passed_attempts.begin(), passed_attempts.end());
    std::set<uint32_t> failed_set(failed_attempts.begin(), failed_attempts.end());

    // Remove from failed those that also passed (different branches)
    for (auto id : passed_set) {
        failed_set.erase(id);
    }

    pass_count += passed_set.size();

    // Clean up stale tokens: once an attempt passes via ANY branch,
    // remove all remaining tokens for that attempt from cons_tokens.
    // This prevents spurious failures when later delay-tokens expire
    // for an attempt that already succeeded.
    if (!passed_set.empty()) {
        cons_tokens.erase(
            std::remove_if(cons_tokens.begin(), cons_tokens.end(),
                [&passed_set](const Token& t) {
                    return passed_set.count(t.attempt_id) > 0;
                }),
            cons_tokens.end());
    }

    if (kind == ASSERT || kind == ASSUME) {
        for (auto id : failed_set) {
            // Check if there are still active tokens for this attempt
            bool still_active = false;
            for (auto& t : cons_tokens) {
                if (t.attempt_id == id) { still_active = true; break; }
            }
            if (!still_active) {
                fail_count++;
                if (fail_times.size() < 100)
                    fail_times.push_back(sim_time);
            }
        }
    } else {
        // COVER: failures just mean the sequence didn't complete — no error
    }
}

// =====================================================================
//  Coverage: CovBin
// =====================================================================
bool CovBin::match_value(int64_t val) const {
    switch (type) {
        case VALUE_RANGE:
        case ILLEGAL:
        case IGNORE:
        case AUTO:
            for (auto& [lo, hi] : ranges) {
                if (val >= lo && val <= hi) return true;
            }
            return false;

        case WILDCARD:
            return ((uint64_t)val & wc_mask) == wc_pattern;

        case DEFAULT:
            return true;  // matches anything not matched by other bins

        case TRANSITION:
            return false;  // transitions use match_transition()
    }
    return false;
}

bool CovBin::match_transition(const std::vector<int64_t>& history) const {
    if (type != TRANSITION) return false;
    for (auto& trans_seq : transitions) {
        if (history.size() >= trans_seq.size()) {
            bool match = true;
            size_t offset = history.size() - trans_seq.size();
            for (size_t i = 0; i < trans_seq.size(); i++) {
                if (history[offset + i] != trans_seq[i]) {
                    match = false;
                    break;
                }
            }
            if (match) return true;
        }
    }
    return false;
}

// =====================================================================
//  Coverage: Coverpoint
// =====================================================================
void Coverpoint::sample(int64_t value) {
    sample_count++;
    last_value = value;

    // Update history for transitions
    value_history.push_back(value);
    if ((int)value_history.size() > MAX_HISTORY) {
        value_history.erase(value_history.begin());
    }

    // Check default last (only if no other bin matched)
    bool any_matched = false;
    int default_idx = -1;

    for (int i = 0; i < (int)bins.size(); i++) {
        auto& bin = bins[i];
        if (bin.type == CovBin::DEFAULT) {
            default_idx = i;
            continue;
        }

        bool matched = false;
        if (bin.type == CovBin::TRANSITION) {
            matched = bin.match_transition(value_history);
        } else {
            matched = bin.match_value(value);
        }

        if (matched) {
            if (bin.type == CovBin::ILLEGAL) {
                // Tracked in bin hit_count; reported in file
            }
            if (bin.type != CovBin::IGNORE) {
                bin.hit_count++;
                any_matched = true;
            } else {
                any_matched = true;  // ignore bins suppress default
            }
        }
    }

    if (!any_matched && default_idx >= 0) {
        bins[default_idx].hit_count++;
    }
}

uint64_t Coverpoint::bins_hit() const {
    uint64_t count = 0;
    for (auto& b : bins) {
        if (b.type != CovBin::IGNORE && b.type != CovBin::ILLEGAL &&
            b.hit_count > 0) {
            count++;
        }
    }
    return count;
}

uint64_t Coverpoint::total_bins() const {
    uint64_t count = 0;
    for (auto& b : bins) {
        if (b.type != CovBin::IGNORE && b.type != CovBin::ILLEGAL) {
            count++;
        }
    }
    return count;
}

double Coverpoint::coverage_pct() const {
    auto total = total_bins();
    if (total == 0) return 100.0;
    return 100.0 * bins_hit() / total;
}

// =====================================================================
//  Coverage: CrossCoverage
// =====================================================================
uint64_t CrossCoverage::bins_hit() const {
    return cross_hits.size();
}

double CrossCoverage::coverage_pct() const {
    if (total_cross_bins == 0) return 100.0;
    return 100.0 * bins_hit() / total_cross_bins;
}

void CrossCoverage::record(const std::vector<int>& bin_indices) {
    cross_hits[bin_indices]++;
}

// =====================================================================
//  Coverage: Covergroup
// =====================================================================
int Covergroup::add_coverpoint(const std::string& cp_name) {
    int idx = (int)coverpoints.size();
    coverpoints.emplace_back();
    coverpoints.back().name = cp_name;
    coverpoints.back().auto_bin_max = auto_bin_max;
    return idx;
}

int Covergroup::add_cross(const std::string& x_name, const std::vector<int>& cp_ids) {
    int idx = (int)crosses.size();
    crosses.emplace_back();
    auto& x = crosses.back();
    x.name = x_name;
    x.coverpoint_indices = cp_ids;

    // Compute total cross bins
    uint64_t total = 1;
    for (auto id : cp_ids) {
        if (id >= 0 && id < (int)coverpoints.size()) {
            auto t = coverpoints[id].total_bins();
            if (t > 0) total *= t;
        }
    }
    x.total_cross_bins = total;

    return idx;
}

void Covergroup::sample() {
    sample_count++;

    // Process cross coverage
    for (auto& x : crosses) {
        // Find which bins matched for each coverpoint
        std::vector<std::vector<int>> matched_bins;
        for (auto cp_id : x.coverpoint_indices) {
            std::vector<int> matches;
            if (cp_id >= 0 && cp_id < (int)coverpoints.size()) {
                auto& cp = coverpoints[cp_id];
                for (int i = 0; i < (int)cp.bins.size(); i++) {
                    if (cp.bins[i].type != CovBin::IGNORE &&
                        cp.bins[i].type != CovBin::ILLEGAL &&
                        cp.bins[i].match_value(cp.last_value)) {
                        matches.push_back(i);
                    }
                }
            }
            matched_bins.push_back(matches);
        }

        // Generate cross products
        if (matched_bins.empty()) continue;

        // Recursive cross product
        std::vector<int> current;
        std::function<void(int)> cross_product = [&](int depth) {
            if (depth == (int)matched_bins.size()) {
                x.record(current);
                return;
            }
            for (auto idx : matched_bins[depth]) {
                current.push_back(idx);
                cross_product(depth + 1);
                current.pop_back();
            }
        };
        cross_product(0);
    }
}

double Covergroup::coverage_pct() const {
    if (coverpoints.empty() && crosses.empty()) return 100.0;
    double sum = 0.0;
    int count = 0;
    for (auto& cp : coverpoints) {
        sum += cp.coverage_pct();
        count++;
    }
    for (auto& x : crosses) {
        sum += x.coverage_pct();
        count++;
    }
    return count ? sum / count : 100.0;
}

// =====================================================================
//  Engine singleton
// =====================================================================
Engine& Engine::instance() {
    static Engine eng;
    return eng;
}

void Engine::set_signal(const std::string& name, int64_t value) {
    signals_[name] = value;
}

int64_t Engine::get_signal(const std::string& name) const {
    auto it = signals_.find(name);
    return (it != signals_.end()) ? it->second : 0;
}

int Engine::create_assertion(const std::string& name, const std::string& prop_expr,
                             int kind, const std::string& file, int line) {
    int id = (int)assertions_.size();
    auto a = std::make_unique<Assertion>();
    a->name = name;
    a->source_file = file;
    a->source_line = line;
    a->kind = static_cast<Assertion::Kind>(kind);
    a->property = parse_property(prop_expr);
    assertions_.push_back(std::move(a));
    return id;
}

Assertion* Engine::get_assertion(int id) {
    if (id >= 0 && id < (int)assertions_.size()) return assertions_[id].get();
    return nullptr;
}

void Engine::tick(uint64_t sim_time) {
    for (auto& a : assertions_) {
        uint64_t prev_fails = a->fail_count;
        a->tick(cycle_, sim_time, signals_, prev_signals_);
        if (fail_callback_ && a->fail_count > prev_fails) {
            std::string msg = a->fail_message.empty()
                ? a->name + " (" + a->source_file + ":" + std::to_string(a->source_line) + ")"
                : a->fail_message;
            fail_callback_(a->name.c_str(), msg.c_str());
        }
    }
    prev_signals_ = signals_;
    cycle_++;
}

int Engine::create_covergroup(const std::string& name) {
    int id = (int)covergroups_.size();
    covergroups_.push_back(std::make_unique<Covergroup>());
    covergroups_.back()->name = name;
    return id;
}

Covergroup* Engine::get_covergroup(int id) {
    if (id >= 0 && id < (int)covergroups_.size()) return covergroups_[id].get();
    return nullptr;
}

int Engine::cg_add_coverpoint(int cg_id, const std::string& cp_name) {
    auto* cg = get_covergroup(cg_id);
    if (!cg) return -1;
    int cp_idx = cg->add_coverpoint(cp_name);

    // Register globally
    int global_id = (int)cp_registry_.size();
    cp_registry_.push_back({cg_id, cp_idx});
    return global_id;
}

void Engine::cg_add_bin(int cp_id, const std::string& bin_name,
                        int64_t lo, int64_t hi) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    CovBin bin;
    bin.name = bin_name;
    bin.type = CovBin::VALUE_RANGE;
    bin.ranges.push_back({lo, hi});
    cp.bins.push_back(std::move(bin));
}

void Engine::cg_add_bin_list(int cp_id, const std::string& bin_name,
                             const std::vector<int64_t>& values) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    CovBin bin;
    bin.name = bin_name;
    bin.type = CovBin::VALUE_RANGE;
    for (auto v : values) {
        bin.ranges.push_back({v, v});
    }
    cp.bins.push_back(std::move(bin));
}

void Engine::cg_add_transition_bin(int cp_id, const std::string& bin_name,
                                   const std::vector<int64_t>& sequence) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    CovBin bin;
    bin.name = bin_name;
    bin.type = CovBin::TRANSITION;
    bin.transitions.push_back(sequence);
    cp.bins.push_back(std::move(bin));
}

void Engine::cg_add_wildcard_bin(int cp_id, const std::string& bin_name,
                                 uint64_t mask, uint64_t pattern) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    CovBin bin;
    bin.name = bin_name;
    bin.type = CovBin::WILDCARD;
    bin.wc_mask = mask;
    bin.wc_pattern = pattern;
    cp.bins.push_back(std::move(bin));
}

void Engine::cg_add_illegal_bin(int cp_id, const std::string& bin_name,
                                int64_t lo, int64_t hi) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    CovBin bin;
    bin.name = bin_name;
    bin.type = CovBin::ILLEGAL;
    bin.ranges.push_back({lo, hi});
    cp.bins.push_back(std::move(bin));
}

void Engine::cg_add_ignore_bin(int cp_id, const std::string& bin_name,
                               int64_t lo, int64_t hi) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    CovBin bin;
    bin.name = bin_name;
    bin.type = CovBin::IGNORE;
    bin.ranges.push_back({lo, hi});
    cp.bins.push_back(std::move(bin));
}

void Engine::cg_add_default_bin(int cp_id, const std::string& bin_name) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    CovBin bin;
    bin.name = bin_name;
    bin.type = CovBin::DEFAULT;
    cp.bins.push_back(std::move(bin));
}

void Engine::cg_add_auto_bins(int cp_id, int count) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    auto& cp = cg->coverpoints[ref.cp_idx];

    cp.has_auto_bins = true;
    cp.auto_bin_max = count;

    // Generate auto bins: [0, count) with equal ranges
    // In real SV, auto_bin_max divides the value range
    for (int i = 0; i < count; i++) {
        CovBin bin;
        bin.name = "auto[" + std::to_string(i) + "]";
        bin.type = CovBin::AUTO;
        bin.ranges.push_back({i, i});
        cp.bins.push_back(std::move(bin));
    }
}

void Engine::cg_sample_point(int cp_id, int64_t value) {
    if (cp_id < 0 || cp_id >= (int)cp_registry_.size()) return;
    auto& ref = cp_registry_[cp_id];
    auto* cg = get_covergroup(ref.cg_idx);
    if (!cg || ref.cp_idx >= (int)cg->coverpoints.size()) return;
    cg->coverpoints[ref.cp_idx].sample(value);
}

void Engine::cg_sample(int cg_id) {
    auto* cg = get_covergroup(cg_id);
    if (cg) cg->sample();
}

int Engine::cg_add_cross(int cg_id, const std::string& name,
                         const std::vector<int>& cp_ids) {
    auto* cg = get_covergroup(cg_id);
    if (!cg) return -1;

    // Convert global cp_ids to local indices within this covergroup
    std::vector<int> local_ids;
    for (auto gid : cp_ids) {
        if (gid >= 0 && gid < (int)cp_registry_.size()) {
            local_ids.push_back(cp_registry_[gid].cp_idx);
        }
    }
    return cg->add_cross(name, local_ids);
}

void Engine::report(const std::string& filename) {
    // Write report to file only — no terminal output
    FILE* fp = fopen(filename.c_str(), "w");
    if (!fp) return;

    fprintf(fp, "================================================================\n");
    fprintf(fp, "  SVA ENGINE REPORT\n");
    fprintf(fp, "================================================================\n");
    fprintf(fp, "  Simulation cycles: %lu\n", (unsigned long)cycle_);
    fprintf(fp, "----------------------------------------------------------------\n");

    // Assertion results
    if (!assertions_.empty()) {
        fprintf(fp, "\n  ASSERTIONS:\n");
        for (auto& a : assertions_) {
            const char* kind_str = (a->kind == Assertion::ASSERT) ? "assert" :
                                   (a->kind == Assertion::COVER) ? "cover" : "assume";
            fprintf(fp, "    [%s] %s", kind_str, a->name.c_str());
            if (!a->source_file.empty())
                fprintf(fp, " (%s:%d)", a->source_file.c_str(), a->source_line);
            fprintf(fp, "\n");
            fprintf(fp, "      pass: %lu  fail: %lu  vacuous: %lu  attempts: %lu\n",
                    (unsigned long)a->pass_count,
                    (unsigned long)a->fail_count,
                    (unsigned long)a->vacuous_count,
                    (unsigned long)a->attempt_count);
            if (!a->fail_times.empty()) {
                fprintf(fp, "      failed @");
                for (auto t : a->fail_times)
                    fprintf(fp, " %lu", (unsigned long)t);
                if (a->fail_count > (uint64_t)a->fail_times.size())
                    fprintf(fp, " ... (%lu more)",
                            (unsigned long)(a->fail_count - a->fail_times.size()));
                fprintf(fp, "\n");
            }
        }
    }

    // Coverage results
    if (!covergroups_.empty()) {
        fprintf(fp, "\n  COVERGROUPS:\n");
        for (auto& cg : covergroups_) {
            fprintf(fp, "\n  covergroup: %s  (%.1f%% coverage, %lu samples)\n",
                    cg->name.c_str(), cg->coverage_pct(),
                    (unsigned long)cg->sample_count);

            for (auto& cp : cg->coverpoints) {
                fprintf(fp, "    coverpoint: %s  (%.1f%% - %lu/%lu bins hit)\n",
                        cp.name.c_str(), cp.coverage_pct(),
                        (unsigned long)cp.bins_hit(),
                        (unsigned long)cp.total_bins());
                for (auto& b : cp.bins) {
                    const char* type_str = "";
                    switch (b.type) {
                        case CovBin::ILLEGAL: type_str = " [ILLEGAL]"; break;
                        case CovBin::IGNORE:  type_str = " [IGNORE]"; break;
                        case CovBin::DEFAULT: type_str = " [DEFAULT]"; break;
                        case CovBin::WILDCARD: type_str = " [WILDCARD]"; break;
                        case CovBin::TRANSITION: type_str = " [TRANS]"; break;
                        default: break;
                    }
                    const char* mark = (b.hit_count > 0) ? " " : "%";
                    fprintf(fp, "      %s %-20s", mark, b.name.c_str());
                    if (!b.ranges.empty()) {
                        fprintf(fp, " {");
                        for (size_t i = 0; i < b.ranges.size(); i++) {
                            if (i) fprintf(fp, ", ");
                            if (b.ranges[i].first == b.ranges[i].second)
                                fprintf(fp, "%ld", (long)b.ranges[i].first);
                            else
                                fprintf(fp, "[%ld:%ld]", (long)b.ranges[i].first,
                                        (long)b.ranges[i].second);
                        }
                        fprintf(fp, "}");
                    }
                    fprintf(fp, "%s  hits: %lu\n", type_str,
                            (unsigned long)b.hit_count);
                }
            }

            for (auto& x : cg->crosses) {
                fprintf(fp, "    cross: %s  (%.1f%% - %lu/%lu bins hit)\n",
                        x.name.c_str(), x.coverage_pct(),
                        (unsigned long)x.bins_hit(),
                        (unsigned long)x.total_cross_bins);
                for (auto& [key, hits] : x.cross_hits) {
                    fprintf(fp, "      <");
                    for (size_t i = 0; i < key.size(); i++) {
                        if (i) fprintf(fp, ", ");
                        int cp_local_idx = x.coverpoint_indices[i];
                        if (cp_local_idx < (int)cg->coverpoints.size() &&
                            key[i] < (int)cg->coverpoints[cp_local_idx].bins.size()) {
                            fprintf(fp, "%s",
                                    cg->coverpoints[cp_local_idx].bins[key[i]].name.c_str());
                        } else {
                            fprintf(fp, "%d", key[i]);
                        }
                    }
                    fprintf(fp, ">  hits: %lu\n", (unsigned long)hits);
                }
            }
        }
    }

    fprintf(fp, "\n================================================================\n");
    fclose(fp);
}

void Engine::reset() {
    cycle_ = 0;
    signals_.clear();
    prev_signals_.clear();
    assertions_.clear();
    covergroups_.clear();
    cp_registry_.clear();
}

}  // namespace sva
