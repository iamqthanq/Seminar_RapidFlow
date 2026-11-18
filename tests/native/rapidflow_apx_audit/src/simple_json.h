#ifndef SIMPLE_JSON_H
#define SIMPLE_JSON_H

#include <cctype>
#include <cstdint>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace simple_json {

enum Type { J_NULL, J_BOOL, J_INT, J_DOUBLE, J_STRING, J_ARRAY, J_OBJECT };

struct Value {
    Type type = J_NULL;
    bool b_val = false;
    int64_t i_val = 0;
    double d_val = 0.0;
    std::string s_val;
    std::vector<Value> arr_val;
    std::map<std::string, Value> obj_val;

    bool is_null() const { return type == J_NULL; }
    bool is_bool() const { return type == J_BOOL; }
    bool is_int() const { return type == J_INT; }
    bool is_double() const { return type == J_DOUBLE; }
    bool is_string() const { return type == J_STRING; }
    bool is_array() const { return type == J_ARRAY; }
    bool is_object() const { return type == J_OBJECT; }

    int64_t as_int() const {
        if (type == J_INT) return i_val;
        if (type == J_DOUBLE) return static_cast<int64_t>(d_val);
        throw std::runtime_error("JSON Value is not an integer");
    }

    bool as_bool() const {
        if (type == J_BOOL) return b_val;
        throw std::runtime_error("JSON Value is not a boolean");
    }

    const std::string& as_string() const {
        if (type == J_STRING) return s_val;
        throw std::runtime_error("JSON Value is not a string");
    }

    double as_double() const {
        if (type == J_DOUBLE) return d_val;
        if (type == J_INT) return static_cast<double>(i_val);
        throw std::runtime_error("JSON Value is not a number");
    }

    size_t size() const {
        if (type == J_ARRAY) return arr_val.size();
        if (type == J_OBJECT) return obj_val.size();
        throw std::runtime_error("JSON size() called on non-container");
    }

    const Value& operator[](const std::string& k) const {
        if (type != J_OBJECT) throw std::runtime_error("JSON operator[](string) called on non-object");
        auto it = obj_val.find(k);
        if (it == obj_val.end()) throw std::runtime_error("JSON key not found: " + k);
        return it->second;
    }

    bool has_key(const std::string& k) const {
        if (type != J_OBJECT) return false;
        return obj_val.find(k) != obj_val.end();
    }

    const Value& operator[](size_t idx) const {
        if (type != J_ARRAY) throw std::runtime_error("JSON operator[](size_t) called on non-array");
        if (idx >= arr_val.size()) throw std::runtime_error("JSON array index out of bounds");
        return arr_val[idx];
    }
};

class Parser {
private:
    const std::string& src;
    size_t pos = 0;

    void skip_whitespace() {
        while (pos < src.size()) {
            char c = src[pos];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                pos++;
            } else {
                break;
            }
        }
    }

    char peek() {
        skip_whitespace();
        if (pos >= src.size()) return '\0';
        return src[pos];
    }

    char get() {
        skip_whitespace();
        if (pos >= src.size()) throw std::runtime_error("Unexpected EOF in JSON input");
        return src[pos++];
    }

    std::string parse_string() {
        if (get() != '"') throw std::runtime_error("Expected '\"' at start of JSON string");
        std::string res;
        while (pos < src.size()) {
            char c = src[pos++];
            if (c == '"') {
                return res;
            }
            if (c == '\\') {
                if (pos >= src.size()) throw std::runtime_error("Unexpected EOF in string escape");
                char esc = src[pos++];
                if (esc == '"') res.push_back('"');
                else if (esc == '\\') res.push_back('\\');
                else if (esc == '/') res.push_back('/');
                else if (esc == 'b') res.push_back('\b');
                else if (esc == 'f') res.push_back('\f');
                else if (esc == 'n') res.push_back('\n');
                else if (esc == 'r') res.push_back('\r');
                else if (esc == 't') res.push_back('\t');
                else res.push_back(esc);
            } else {
                res.push_back(c);
            }
        }
        throw std::runtime_error("Unterminated string in JSON");
    }

    Value parse_number() {
        skip_whitespace();
        size_t start = pos;
        if (pos < src.size() && src[pos] == '-') {
            pos++;
        }
        bool is_float = false;
        while (pos < src.size()) {
            char c = src[pos];
            if (std::isdigit(static_cast<unsigned char>(c))) {
                pos++;
            } else if (c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') {
                is_float = true;
                pos++;
            } else {
                break;
            }
        }
        std::string num_str = src.substr(start, pos - start);
        Value v;
        if (is_float) {
            v.type = J_DOUBLE;
            v.d_val = std::stod(num_str);
        } else {
            v.type = J_INT;
            v.i_val = std::stoll(num_str);
        }
        return v;
    }

public:
    explicit Parser(const std::string& s) : src(s) {}

    Value parse_value() {
        char c = peek();
        if (c == '"') {
            Value v;
            v.type = J_STRING;
            v.s_val = parse_string();
            return v;
        } else if (c == '{') {
            get(); // eat '{'
            Value v;
            v.type = J_OBJECT;
            skip_whitespace();
            if (peek() == '}') {
                get(); // empty object
                return v;
            }
            while (true) {
                std::string key = parse_string();
                if (get() != ':') throw std::runtime_error("Expected ':' after object key");
                v.obj_val[key] = parse_value();
                char next_c = peek();
                if (next_c == ',') {
                    get();
                } else if (next_c == '}') {
                    get();
                    break;
                } else {
                    throw std::runtime_error("Expected ',' or '}' in object");
                }
            }
            return v;
        } else if (c == '[') {
            get(); // eat '['
            Value v;
            v.type = J_ARRAY;
            skip_whitespace();
            if (peek() == ']') {
                get(); // empty array
                return v;
            }
            while (true) {
                v.arr_val.push_back(parse_value());
                char next_c = peek();
                if (next_c == ',') {
                    get();
                } else if (next_c == ']') {
                    get();
                    break;
                } else {
                    throw std::runtime_error("Expected ',' or ']' in array");
                }
            }
            return v;
        } else if (c == 't' || c == 'f') {
            std::string b;
            while (std::isalpha(static_cast<unsigned char>(peek()))) {
                b.push_back(get());
            }
            Value v;
            v.type = J_BOOL;
            if (b == "true") v.b_val = true;
            else if (b == "false") v.b_val = false;
            else throw std::runtime_error("Invalid boolean literal: " + b);
            return v;
        } else if (c == 'n') {
            std::string n;
            while (std::isalpha(static_cast<unsigned char>(peek()))) {
                n.push_back(get());
            }
            if (n != "null") throw std::runtime_error("Invalid null literal: " + n);
            Value v;
            v.type = J_NULL;
            return v;
        } else if (std::isdigit(static_cast<unsigned char>(c)) || c == '-') {
            return parse_number();
        } else {
            throw std::runtime_error(std::string("Unexpected character in JSON: '") + c + "'");
        }
    }
};

inline Value parse(const std::string& json_str) {
    Parser p(json_str);
    return p.parse_value();
}

inline Value parse_file(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) {
        throw std::runtime_error("Could not open JSON file: " + path);
    }
    std::stringstream buf;
    buf << f.rdbuf();
    return parse(buf.str());
}

} // namespace simple_json

#endif // SIMPLE_JSON_H
