#ifndef RAPIDFLOW_APX_JSON_MINI_H
#define RAPIDFLOW_APX_JSON_MINI_H

#include <string>
#include <vector>
#include <unordered_map>
#include <sstream>
#include <stdexcept>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <utility>

namespace rapidflow_apx {
namespace json {

enum class Type { Null, Bool, Int, Float, String, Array, Object };

struct Value {
    Type type{Type::Null};
    bool b_val{false};
    int64_t i_val{0};
    double f_val{0.0};
    std::string s_val{};
    std::vector<Value> arr{};
    std::unordered_map<std::string, Value> obj{};

    Value() = default;
    Value(std::nullptr_t) : type(Type::Null) {}
    Value(bool b) : type(Type::Bool), b_val(b) {}
    Value(int v) : type(Type::Int), i_val(v) {}
    Value(long v) : type(Type::Int), i_val(static_cast<int64_t>(v)) {}
    Value(long long v) : type(Type::Int), i_val(static_cast<int64_t>(v)) {}
    Value(unsigned int v) : type(Type::Int), i_val(static_cast<int64_t>(v)) {}
    Value(unsigned long v) : type(Type::Int), i_val(static_cast<int64_t>(v)) {}
    Value(unsigned long long v) : type(Type::Int), i_val(static_cast<int64_t>(v)) {}
    Value(double v) : type(Type::Float), f_val(v) {}
    Value(const char* s) : type(Type::String), s_val(s) {}
    Value(const std::string& s) : type(Type::String), s_val(s) {}
    Value(std::string&& s) noexcept : type(Type::String), s_val(std::move(s)) {}

    static Value make_array() {
        Value v;
        v.type = Type::Array;
        return v;
    }

    static Value make_object() {
        Value v;
        v.type = Type::Object;
        return v;
    }

    bool is_null() const { return type == Type::Null; }
    bool is_bool() const { return type == Type::Bool; }
    bool is_int() const { return type == Type::Int; }
    bool is_float() const { return type == Type::Float; }
    bool is_string() const { return type == Type::String; }
    bool is_array() const { return type == Type::Array; }
    bool is_object() const { return type == Type::Object; }

    bool as_bool() const {
        if (type != Type::Bool) {
            throw std::runtime_error("JSON type error: expected Bool");
        }
        return b_val;
    }

    int64_t as_int() const {
        if (type != Type::Int) {
            throw std::runtime_error("JSON type error: expected Int");
        }
        return i_val;
    }

    double as_float() const {
        if (type == Type::Float) return f_val;
        if (type == Type::Int) return static_cast<double>(i_val);
        throw std::runtime_error("JSON type error: expected Float/Number");
    }

    const std::string& as_string() const {
        if (type != Type::String) {
            throw std::runtime_error("JSON type error: expected String");
        }
        return s_val;
    }

    const std::vector<Value>& as_array() const {
        if (type != Type::Array) {
            throw std::runtime_error("JSON type error: expected Array");
        }
        return arr;
    }

    const std::unordered_map<std::string, Value>& as_object() const {
        if (type != Type::Object) {
            throw std::runtime_error("JSON type error: expected Object");
        }
        return obj;
    }

    bool contains(const std::string& key) const {
        return type == Type::Object && obj.find(key) != obj.end();
    }

    const Value& operator[](const std::string& key) const {
        if (type != Type::Object) {
            throw std::runtime_error("JSON operator[] on non-object");
        }
        auto it = obj.find(key);
        if (it == obj.end()) throw std::out_of_range("JSON key not found: " + key);
        return it->second;
    }

    Value& operator[](const std::string& key) {
        if (type != Type::Object) {
            type = Type::Object;
            obj.clear();
        }
        return obj[key];
    }

    const Value& operator[](size_t idx) const {
        if (type != Type::Array) {
            throw std::runtime_error("JSON operator[] on non-array");
        }
        if (idx >= arr.size()) throw std::out_of_range("JSON array index out of range");
        return arr[idx];
    }

    void push_back(const Value& item) {
        if (type != Type::Array) {
            type = Type::Array;
            arr.clear();
        }
        arr.push_back(item);
    }
};

class Parser {
public:
    static Value parse(const std::string& src) {
        size_t pos = 0;
        skip_whitespace(src, pos);
        Value result = parse_value(src, pos);
        skip_whitespace(src, pos);
        if (pos < src.size()) {
            throw std::runtime_error("JSON parse error: trailing characters after root value");
        }
        return result;
    }

private:
    static void skip_whitespace(const std::string& s, size_t& pos) {
        while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t' || s[pos] == '\n' || s[pos] == '\r')) {
            pos++;
        }
    }

    static Value parse_value(const std::string& s, size_t& pos) {
        skip_whitespace(s, pos);
        if (pos >= s.size()) {
            throw std::runtime_error("JSON parse error: unexpected end of input");
        }

        char c = s[pos];
        if (c == 'n') return parse_null(s, pos);
        if (c == 't' || c == 'f') return parse_bool(s, pos);
        if (c == '"') return parse_string(s, pos);
        if (c == '[') return parse_array(s, pos);
        if (c == '{') return parse_object(s, pos);
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) return parse_number(s, pos);

        throw std::runtime_error(std::string("JSON parse error: unexpected character '") + c + "'");
    }

    static Value parse_null(const std::string& s, size_t& pos) {
        if (s.compare(pos, 4, "null") == 0) {
            pos += 4;
            return Value(nullptr);
        }
        throw std::runtime_error("JSON parse error: expected 'null'");
    }

    static Value parse_bool(const std::string& s, size_t& pos) {
        if (s.compare(pos, 4, "true") == 0) {
            pos += 4;
            return Value(true);
        }
        if (s.compare(pos, 5, "false") == 0) {
            pos += 5;
            return Value(false);
        }
        throw std::runtime_error("JSON parse error: expected bool");
    }

    static uint32_t read_hex_quad(const std::string& s, size_t& pos) {
        if (s.size() - pos < 4) {
            throw std::invalid_argument("JSON parse error: incomplete Unicode escape");
        }
        uint32_t value = 0;
        for (int i = 0; i < 4; ++i) {
            const char digit = s[pos++];
            value <<= 4;
            if (digit >= '0' && digit <= '9') value += digit - '0';
            else if (digit >= 'a' && digit <= 'f') value += digit - 'a' + 10;
            else if (digit >= 'A' && digit <= 'F') value += digit - 'A' + 10;
            else throw std::invalid_argument("JSON parse error: invalid Unicode hex digit");
        }
        return value;
    }

    static void append_utf8(std::string& out, uint32_t scalar) {
        if (scalar < 0x80) {
            out += static_cast<char>(scalar);
            return;
        }
        if (scalar >= 0x10000) {
            out += static_cast<char>(0xF0 | (scalar >> 18));
            out += static_cast<char>(0x80 | ((scalar >> 12) & 0x3F));
        } else if (scalar >= 0x800) {
            out += static_cast<char>(0xE0 | (scalar >> 12));
        } else {
            out += static_cast<char>(0xC0 | (scalar >> 6));
        }
        if (scalar >= 0x800) out += static_cast<char>(0x80 | ((scalar >> 6) & 0x3F));
        out += static_cast<char>(0x80 | (scalar & 0x3F));
    }

    static uint32_t read_unicode_escape(const std::string& s, size_t& pos) {
        const uint32_t first = read_hex_quad(s, pos);
        if (first < 0xD800 || first > 0xDFFF) return first;
        if (first >= 0xDC00 || s.compare(pos, 2, "\\u") != 0) {
            throw std::invalid_argument("JSON parse error: unpaired Unicode surrogate");
        }
        pos += 2;
        const uint32_t second = read_hex_quad(s, pos);
        if (second < 0xDC00 || second > 0xDFFF) {
            throw std::invalid_argument("JSON parse error: invalid Unicode surrogate pair");
        }
        return 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00);
    }

    static void copy_utf8(const std::string& s, size_t& pos, std::string& out) {
        const size_t start = pos;
        const auto lead = static_cast<unsigned char>(s[pos++]);
        uint32_t scalar;
        uint32_t minimum;
        size_t remaining;
        if (lead >= 0xC2 && lead <= 0xDF) {
            scalar = lead & 0x1F;
            minimum = 0x80;
            remaining = 1;
        } else if (lead >= 0xE0 && lead <= 0xEF) {
            scalar = lead & 0x0F;
            minimum = 0x800;
            remaining = 2;
        } else if (lead >= 0xF0 && lead <= 0xF4) {
            scalar = lead & 0x07;
            minimum = 0x10000;
            remaining = 3;
        } else {
            throw std::invalid_argument("JSON parse error: invalid UTF-8 lead byte");
        }
        if (s.size() - pos < remaining) {
            throw std::invalid_argument("JSON parse error: truncated UTF-8");
        }
        for (size_t i = 0; i < remaining; ++i) {
            const auto byte = static_cast<unsigned char>(s[pos++]);
            if ((byte & 0xC0) != 0x80) {
                throw std::invalid_argument("JSON parse error: invalid UTF-8 continuation");
            }
            scalar = (scalar << 6) | (byte & 0x3F);
        }
        if (scalar < minimum || scalar > 0x10FFFF || (scalar >= 0xD800 && scalar <= 0xDFFF)) {
            throw std::invalid_argument("JSON parse error: invalid UTF-8 scalar");
        }
        out.append(s, start, pos - start);
    }

    static Value parse_string(const std::string& s, size_t& pos) {
        ++pos; // opening quote
        std::string res;
        while (pos < s.size()) {
            const auto c = static_cast<unsigned char>(s[pos]);
            if (c >= 0x80) {
                copy_utf8(s, pos, res);
                continue;
            }
            ++pos;
            if (c == '"') return Value(std::move(res));
            if (c < 0x20) {
                throw std::invalid_argument("JSON parse error: unescaped control character");
            }
            if (c != '\\') {
                res += static_cast<char>(c);
                continue;
            }
            if (pos == s.size()) throw std::invalid_argument("JSON parse error: incomplete escape");
            switch (s[pos++]) {
                case '"': res += '"'; break;
                case '\\': res += '\\'; break;
                case '/': res += '/'; break;
                case 'b': res += '\b'; break;
                case 'f': res += '\f'; break;
                case 'n': res += '\n'; break;
                case 'r': res += '\r'; break;
                case 't': res += '\t'; break;
                case 'u': append_utf8(res, read_unicode_escape(s, pos)); break;
                default: throw std::invalid_argument("JSON parse error: unsupported escape");
            }
        }
        throw std::invalid_argument("JSON parse error: unterminated string");
    }

    static Value parse_number(const std::string& s, size_t& pos) {
        size_t start = pos;
        bool is_float = false;
        if (s[pos] == '-') pos++;
        while (pos < s.size() && std::isdigit(static_cast<unsigned char>(s[pos]))) {
            pos++;
        }
        if (pos < s.size() && s[pos] == '.') {
            is_float = true;
            pos++;
            while (pos < s.size() && std::isdigit(static_cast<unsigned char>(s[pos]))) {
                pos++;
            }
        }
        if (pos < s.size() && (s[pos] == 'e' || s[pos] == 'E')) {
            is_float = true;
            pos++;
            if (pos < s.size() && (s[pos] == '+' || s[pos] == '-')) pos++;
            while (pos < s.size() && std::isdigit(static_cast<unsigned char>(s[pos]))) {
                pos++;
            }
        }

        std::string num_str = s.substr(start, pos - start);
        if (is_float) {
            return Value(std::stod(num_str));
        } else {
            return Value(std::stoll(num_str));
        }
    }

    static Value parse_array(const std::string& s, size_t& pos) {
        pos++; // skip '['
        Value arr = Value::make_array();
        skip_whitespace(s, pos);
        if (pos < s.size() && s[pos] == ']') {
            pos++;
            return arr;
        }

        while (pos < s.size()) {
            arr.push_back(parse_value(s, pos));
            skip_whitespace(s, pos);
            if (pos >= s.size()) break;
            if (s[pos] == ']') {
                pos++;
                return arr;
            }
            if (s[pos] == ',') {
                pos++;
                skip_whitespace(s, pos);
            } else {
                throw std::runtime_error(std::string("JSON parse error: expected ',' or ']' in array, got '") + s[pos] + "'");
            }
        }
        throw std::runtime_error("JSON parse error: unterminated array");
    }

    static Value parse_object(const std::string& s, size_t& pos) {
        pos++; // skip '{'
        Value obj = Value::make_object();
        skip_whitespace(s, pos);
        if (pos < s.size() && s[pos] == '}') {
            pos++;
            return obj;
        }

        while (pos < s.size()) {
            skip_whitespace(s, pos);
            if (pos >= s.size() || s[pos] != '"') {
                throw std::runtime_error("JSON parse error: expected string key in object");
            }
            Value key_val = parse_string(s, pos);
            std::string key = key_val.as_string();

            skip_whitespace(s, pos);
            if (pos >= s.size() || s[pos] != ':') {
                throw std::runtime_error("JSON parse error: expected ':' after key");
            }
            pos++; // skip ':'

            obj[key] = parse_value(s, pos);
            skip_whitespace(s, pos);
            if (pos >= s.size()) break;
            if (s[pos] == '}') {
                pos++;
                return obj;
            }
            if (s[pos] == ',') {
                pos++;
                skip_whitespace(s, pos);
            } else {
                throw std::runtime_error(std::string("JSON parse error: expected ',' or '}' in object, got '") + s[pos] + "'");
            }
        }
        throw std::runtime_error("JSON parse error: unterminated object");
    }
};

inline std::string escape_string(const std::string& s) {
    std::ostringstream o;
    o << '"';
    for (char c : s) {
        switch (c) {
            case '"': o << "\\\""; break;
            case '\\': o << "\\\\"; break;
            case '\b': o << "\\b"; break;
            case '\f': o << "\\f"; break;
            case '\n': o << "\\n"; break;
            case '\r': o << "\\r"; break;
            case '\t': o << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) <= 0x1f) {
                    o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
                } else {
                    o << c;
                }
        }
    }
    o << '"';
    return o.str();
}

inline std::string serialize(const Value& v, int indent = 0) {
    std::string ind(indent * 2, ' ');
    std::string ind_next((indent + 1) * 2, ' ');

    switch (v.type) {
        case Type::Null: return "null";
        case Type::Bool: return v.b_val ? "true" : "false";
        case Type::Int: return std::to_string(v.i_val);
        case Type::Float: {
            std::ostringstream ss;
            ss << std::setprecision(10) << v.f_val;
            return ss.str();
        }
        case Type::String: return escape_string(v.s_val);
        case Type::Array: {
            if (v.arr.empty()) return "[]";
            // Check if array of primitives
            bool all_primitives = true;
            for (const auto& el : v.arr) {
                if (el.is_array() || el.is_object()) {
                    all_primitives = false;
                    break;
                }
            }
            if (all_primitives && v.arr.size() <= 8) {
                std::string s = "[";
                for (size_t i = 0; i < v.arr.size(); ++i) {
                    s += serialize(v.arr[i], 0);
                    if (i + 1 < v.arr.size()) s += ", ";
                }
                s += "]";
                return s;
            }
            std::string s = "[\n";
            for (size_t i = 0; i < v.arr.size(); ++i) {
                s += ind_next + serialize(v.arr[i], indent + 1);
                if (i + 1 < v.arr.size()) s += ",";
                s += "\n";
            }
            s += ind + "]";
            return s;
        }
        case Type::Object: {
            if (v.obj.empty()) return "{}";
            std::string s = "{\n";
            size_t count = 0;
            for (const auto& kv : v.obj) {
                s += ind_next + escape_string(kv.first) + ": " + serialize(kv.second, indent + 1);
                if (++count < v.obj.size()) s += ",";
                s += "\n";
            }
            s += ind + "}";
            return s;
        }
    }
    return "null";
}

} // namespace json
} // namespace rapidflow_apx

#endif // RAPIDFLOW_APX_JSON_MINI_H
