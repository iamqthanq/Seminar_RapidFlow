#include "rapidflow_apx/json_mini.h"

#include <iostream>
#include <string>

using rapidflow_apx::json::Parser;
using rapidflow_apx::json::serialize;

int main() {
    int cases = 0;
    const auto check = [&](const char* id, bool ok) {
        if (!ok) throw std::runtime_error(std::string(id) + " failed");
        ++cases;
    };
    const auto label = [](const std::string& input) {
        return Parser::parse(input).as_string();
    };
    const auto rejects = [&](const char* id, const std::string& input) {
        try {
            Parser::parse(input);
        } catch (const std::invalid_argument&) {
            check(id, true);
            return;
        }
        check(id, false);
    };
    try {
        check("UNI-01", label(R"("\u00e1")") != label(R"("\u00e0")"));
        check("UNI-02", label(R"("á")") == label(R"("\u00e1")"));
        check("UNI-03", label(R"("😀")") == label(R"("\ud83d\ude00")"));
        check("UNI-04", label(R"("\ud83d\ude00")") != label(R"("\ud83d\ude01")"));
        check("UNI-05", label(R"("\u0041")") == label(R"("A")"));
        check("UNI-06", label(R"("?")") != label(R"("\u00e1")"));
        check("UNI-07", label(R"("\u003f")") == label(R"("?")"));
        check("UNI-08", Parser::parse("0").is_int() && Parser::parse(R"("0")").is_string());
        check("UNI-09", label(R"("ắ")") != label(R"("a\u0306\u0301")"));
        rejects("UNI-10", R"("\u00x1")");
        rejects("UNI-11", "\"\\u12");
        rejects("UNI-12", R"("\ud83d")");
        rejects("UNI-13", R"("\ude00")");
        rejects("UNI-14", R"("\ud83d\u00e1")");
        rejects("UNI-15", R"("\?")");
        rejects("UNI-16", "\"a\nb\"");
        rejects("UNI-17 stray continuation", "\"\x80\"");
        rejects("UNI-17 overlong", "\"\xc0\x80\"");
        rejects("UNI-17 truncated", "\"\xf0\x9f");
        rejects("UNI-17 wrong continuation", "\"\xe2\x28\xa1\"");
        rejects("UNI-17 UTF-8 surrogate", "\"\xed\xa0\x80\"");
        rejects("UNI-17 beyond scalar limit", "\"\xf4\x90\x80\x80\"");
        auto controls = Parser::parse(R"("a\u0000\u0001\b\t\n\f\r")");
        check("UNI-18", controls.as_string() == std::string("a\0\1\b\t\n\f\r", 8)
            && label(serialize(controls)) == controls.as_string());
        auto scalar_boundaries = Parser::parse(R"("\u007f\u0080\u07ff\u0800\ud7ff\ue000\udbff\udfff")");
        check("scalar boundaries", scalar_boundaries.as_string() ==
            "\x7f\xc2\x80\xdf\xbf\xe0\xa0\x80\xed\x9f\xbf\xee\x80\x80\xf4\x8f\xbf\xbf"
            && label(serialize(scalar_boundaries)) == scalar_boundaries.as_string());
        const auto object = Parser::parse(R"({"\u00e1":"\ud83d\ude00"})");
        check("object key identity", object["á"].as_string() == "😀");
        std::cout << cases << " JSON string assertions passed (UNI-01 through UNI-18)\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
