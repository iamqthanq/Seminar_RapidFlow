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
        Value val = Value::make_object();
        pos++; // Skip '{'
        skip_whitespace(s, pos);

        if (pos < s.size() && s[pos] == '}') {
            pos++;
            return val;
        }

        while (pos < s.size()) {
            skip_whitespace(s, pos);
            if (pos >= s.size() || s[pos] != '"') {
                throw std::runtime_error("Expected string key in object");
            }
            Value key_val = parse_string(s, pos);
            std::string key = key_val.s_val;

            skip_whitespace(s, pos);
            if (pos >= s.size() || s[pos] != ':') {
                throw std::runtime_error("Expected ':' after key in object");
            }
            pos++; // Skip ':'

            Value member = parse_value(s, pos);
            val.obj[key] = member;

            skip_whitespace(s, pos);
            if (pos >= s.size()) throw std::runtime_error("Unterminated object");
            if (s[pos] == '}') {
                pos++;
                return val;
            }
            if (s[pos] == ',') {
                pos++;
            } else {
                throw std::runtime_error("Expected ',' or '}' in object");
            }
        }
        throw std::runtime_error("Unterminated object");
    }

    static Value parse_array(const std::string& s, size_t& pos) {
        Value val = Value::make_array();
        pos++; // Skip '['
        skip_whitespace(s, pos);

        if (pos < s.size() && s[pos] == ']') {
            pos++;
            return val;
        }

        while (pos < s.size()) {
            val.arr.push_back(parse_value(s, pos));
            skip_whitespace(s, pos);
            if (pos >= s.size()) throw std::runtime_error("Unterminated array");
            if (s[pos] == ']') {
                pos++;
                return val;
            }
            if (s[pos] == ',') {
                pos++;
            } else {
                throw std::runtime_error("Expected ',' or ']' in array");
            }
        }
        throw std::runtime_error("Unterminated array");
    }

    static Value parse_string(const std::string& s, size_t& pos) {
        pos++; // Skip opening '"'
        std::string result;
        while (pos < s.size()) {
            char c = s[pos++];
            if (c == '"') {
                Value v;
                v.type = Type::String;
                v.s_val = result;
                return v;
            }
            if (c == '\\') {
                if (pos >= s.size()) throw std::runtime_error("Unterminated escape sequence");
                char esc = s[pos++];
                switch (esc) {
                    case '"': result += '"'; break;
                    case '\\': result += '\\'; break;
                    case '/': result += '/'; break;
                    case 'b': result += '\b'; break;
                    case 'f': result += '\f'; break;
                    case 'n': result += '\n'; break;
                    case 'r': result += '\r'; break;
                    case 't': result += '\t'; break;
                    case 'u': {
                        if (pos + 4 > s.size()) {
                            throw std::runtime_error("Invalid unicode escape sequence: incomplete hex digits");
                        }
                        uint32_t cp = 0;
                        for (int i = 0; i < 4; ++i) {
                            char h = s[pos++];
                            cp <<= 4;
                            if (h >= '0' && h <= '9') cp |= static_cast<uint32_t>(h - '0');
                            else if (h >= 'a' && h <= 'f') cp |= static_cast<uint32_t>(h - 'a' + 10);
                            else if (h >= 'A' && h <= 'F') cp |= static_cast<uint32_t>(h - 'A' + 10);
                            else throw std::runtime_error(std::string("Invalid hex digit in \\u escape: ") + h);
                        }
                        if (cp <= 0x7F) {
                            result += static_cast<char>(cp);
                        } else if (cp <= 0x7FF) {
                            result += static_cast<char>(0xC0 | ((cp >> 6) & 0x1F));
                            result += static_cast<char>(0x80 | (cp & 0x3F));
                        } else if (cp <= 0xFFFF) {
                            result += static_cast<char>(0xE0 | ((cp >> 12) & 0x0F));
                            result += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                            result += static_cast<char>(0x80 | (cp & 0x3F));
                        } else {
                            result += static_cast<char>(0xF0 | ((cp >> 18) & 0x07));
                            result += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
                            result += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                            result += static_cast<char>(0x80 | (cp & 0x3F));
                        }
                        break;
                    }
                    default:
                        throw std::runtime_error(std::string("Invalid escape sequence: \\") + esc);
                }
            } else {
                result += c;
            }
        }
        throw std::runtime_error("Unterminated string");
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
        throw std::runtime_error("Invalid boolean literal in JSON");
    }

    static Value parse_null(const std::string& s, size_t& pos) {
        if (s.compare(pos, 4, "null") == 0) {
            pos += 4;
            return Value(nullptr);
        }
        throw std::runtime_error("Invalid null literal in JSON");
    }

    static Value parse_number(const std::string& s, size_t& pos) {
        size_t start = pos;
        if (s[pos] == '-') pos++;
        bool is_float = false;
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
            return Value(static_cast<int64_t>(std::stoll(num_str)));
        }
    }
};

inline std::string escape_string(const std::string& s) {
    std::ostringstream ss;
    ss << '"';
    for (char c : s) {
        switch (c) {
            case '"': ss << "\\\""; break;
            case '\\': ss << "\\\\"; break;
            case '\b': ss << "\\b"; break;
            case '\f': ss << "\\f"; break;
            case '\n': ss << "\\n"; break;
            case '\r': ss << "\\r"; break;
            case '\t': ss << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    ss << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
                } else {
                    ss << c;
                }
                break;
        }
    }
    ss << '"';
    return ss.str();
}

inline std::string serialize(const Value& val, int indent = 0) {
    std::ostringstream ss;
    switch (val.type) {
        case Type::Null: ss << "null"; break;
        case Type::Bool: ss << (val.b_val ? "true" : "false"); break;
        case Type::Int: ss << val.i_val; break;
        case Type::Float: ss << val.f_val; break;
        case Type::String: ss << escape_string(val.s_val); break;
        case Type::Array: {
            ss << "[";
            for (size_t i = 0; i < val.arr.size(); ++i) {
                if (i > 0) ss << ", ";
                ss << serialize(val.arr[i], indent);
            }
            ss << "]";
            break;
        }
        case Type::Object: {
            ss << "{\n";
            std::string pad(indent + 2, ' ');
            size_t i = 0;
            for (const auto& kv : val.obj) {
                if (i > 0) ss << ",\n";
                ss << pad << escape_string(kv.first) << ": " << serialize(kv.second, indent + 2);
                i++;
            }
            ss << "\n" << std::string(indent, ' ') << "}";
            break;
        }
    }
    return ss.str();
}

} // namespace mini_json

#endif // JSON_MINI_H
