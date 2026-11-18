#ifndef JSON_MINI_H
#define JSON_MINI_H

#include <string>
#include <vector>
#include <unordered_map>
#include <sstream>
#include <stdexcept>
#include <cctype>
#include <cstdint>
#include <iomanip>

namespace mini_json {

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
    Value(int64_t v) : type(Type::Int), i_val(v) {}
    Value(uint32_t v) : type(Type::Int), i_val(static_cast<int64_t>(v)) {}
    Value(uint64_t v) : type(Type::Int), i_val(static_cast<int64_t>(v)) {}
    Value(double v) : type(Type::Float), f_val(v) {}
    Value(const char* s) : type(Type::String), s_val(s) {}
    Value(const std::string& s) : type(Type::String), s_val(s) {}

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

    bool as_bool() const { return b_val; }
    int64_t as_int() const { return type == Type::Int ? i_val : static_cast<int64_t>(f_val); }
    double as_float() const { return type == Type::Float ? f_val : static_cast<double>(i_val); }
    const std::string& as_string() const { return s_val; }
    const std::vector<Value>& as_array() const { return arr; }
    const std::unordered_map<std::string, Value>& as_object() const { return obj; }

    bool contains(const std::string& key) const {
        return type == Type::Object && obj.find(key) != obj.end();
    }

    const Value& operator[](const std::string& key) const {
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
        return result;
    }

private:
    static void skip_whitespace(const std::string& s, size_t& pos) {
        while (pos < s.size() && (std::isspace(static_cast<unsigned char>(s[pos])) || s[pos] == '\r' || s[pos] == '\n')) {
            pos++;
        }
    }

    static Value parse_value(const std::string& s, size_t& pos) {
        skip_whitespace(s, pos);
        if (pos >= s.size()) throw std::runtime_error("Unexpected end of JSON input");

        char c = s[pos];
        if (c == '{') return parse_object(s, pos);
        if (c == '[') return parse_array(s, pos);
        if (c == '"') return parse_string(s, pos);
        if (c == 't' || c == 'f') return parse_bool(s, pos);
        if (c == 'n') return parse_null(s, pos);
        if (c == '-' || std::isdigit(static_cast<unsigned char>(c))) return parse_number(s, pos);

        throw std::runtime_error(std::string("Unexpected character in JSON: '") + c + "'");
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
                throw std::runtime_error("Expected string key in object");
            }
            std::string key = parse_string(s, pos).as_string();
            skip_whitespace(s, pos);
            if (pos >= s.size() || s[pos] != ':') {
                throw std::runtime_error("Expected ':' after key");
            }
            pos++; // skip ':'
            Value val = parse_value(s, pos);
            obj.obj[key] = std::move(val);

            skip_whitespace(s, pos);
            if (pos < s.size() && s[pos] == ',') {
                pos++;
                continue;
            } else if (pos < s.size() && s[pos] == '}') {
                pos++;
                break;
            } else {
                throw std::runtime_error("Expected ',' or '}' in object");
            }
        }
        return obj;
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
            Value val = parse_value(s, pos);
            arr.arr.push_back(std::move(val));
            skip_whitespace(s, pos);
            if (pos < s.size() && s[pos] == ',') {
                pos++;
                continue;
            } else if (pos < s.size() && s[pos] == ']') {
                pos++;
                break;
            } else {
                throw std::runtime_error("Expected ',' or ']' in array");
            }
        }
        return arr;
    }

    static Value parse_string(const std::string& s, size_t& pos) {
        pos++; // skip opening '"'
        std::string res;
        while (pos < s.size()) {
            char c = s[pos++];
            if (c == '"') return Value(res);
            if (c == '\\') {
                if (pos >= s.size()) throw std::runtime_error("Unterminated escape sequence in string");
                char esc = s[pos++];
                switch (esc) {
                    case '"': res.push_back('"'); break;
                    case '\\': res.push_back('\\'); break;
                    case '/': res.push_back('/'); break;
                    case 'b': res.push_back('\b'); break;
                    case 'f': res.push_back('\f'); break;
                    case 'n': res.push_back('\n'); break;
                    case 'r': res.push_back('\r'); break;
                    case 't': res.push_back('\t'); break;
                    default: res.push_back(esc); break;
                }
            } else {
                res.push_back(c);
            }
        }
        throw std::runtime_error("Unterminated string");
    }

    static Value parse_number(const std::string& s, size_t& pos) {
        size_t start = pos;
        if (s[pos] == '-') pos++;
        bool is_float = false;
        while (pos < s.size() && std::isdigit(static_cast<unsigned char>(s[pos]))) pos++;
        if (pos < s.size() && s[pos] == '.') {
            is_float = true;
            pos++;
            while (pos < s.size() && std::isdigit(static_cast<unsigned char>(s[pos]))) pos++;
        }
        if (pos < s.size() && (s[pos] == 'e' || s[pos] == 'E')) {
            is_float = true;
            pos++;
            if (pos < s.size() && (s[pos] == '+' || s[pos] == '-')) pos++;
            while (pos < s.size() && std::isdigit(static_cast<unsigned char>(s[pos]))) pos++;
        }
        std::string num_str = s.substr(start, pos - start);
        if (is_float) {
            return Value(std::stod(num_str));
        } else {
            return Value(static_cast<int64_t>(std::stoll(num_str)));
        }
    }

    static Value parse_bool(const std::string& s, size_t& pos) {
        if (s.substr(pos, 4) == "true") {
            pos += 4;
            return Value(true);
        }
        if (s.substr(pos, 5) == "false") {
            pos += 5;
            return Value(false);
        }
        throw std::runtime_error("Expected bool at pos " + std::to_string(pos));
    }

    static Value parse_null(const std::string& s, size_t& pos) {
        if (s.substr(pos, 4) == "null") {
            pos += 4;
            return Value(nullptr);
        }
        throw std::runtime_error("Expected null at pos " + std::to_string(pos));
    }
};

inline std::string serialize(const Value& val) {
    switch (val.type) {
        case Type::Null: return "null";
        case Type::Bool: return val.b_val ? "true" : "false";
        case Type::Int: return std::to_string(val.i_val);
        case Type::Float: {
            std::ostringstream ss;
            ss << std::setprecision(10) << val.f_val;
            return ss.str();
        }
        case Type::String: {
            std::string out = "\"";
            for (char c : val.s_val) {
                if (c == '"') out += "\\\"";
                else if (c == '\\') out += "\\\\";
                else if (c == '\n') out += "\\n";
                else if (c == '\r') out += "\\r";
                else if (c == '\t') out += "\\t";
                else out += c;
            }
            out += "\"";
            return out;
        }
        case Type::Array: {
            std::string out = "[";
            for (size_t i = 0; i < val.arr.size(); ++i) {
                if (i > 0) out += ", ";
                out += serialize(val.arr[i]);
            }
            out += "]";
            return out;
        }
        case Type::Object: {
            std::string out = "{";
            bool first = true;
            for (const auto& kv : val.obj) {
                if (!first) out += ", ";
                first = false;
                out += "\"" + kv.first + "\": " + serialize(kv.second);
            }
            out += "}";
            return out;
        }
    }
    return "null";
}

} // namespace mini_json

#endif // JSON_MINI_H
