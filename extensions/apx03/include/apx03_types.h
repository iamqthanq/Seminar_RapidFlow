#ifndef APX03_TYPES_H
#define APX03_TYPES_H

#include <cstdint>
#include <string>
#include <vector>
#include <map>
#include <stdexcept>
#include <sstream>

namespace apx03 {

enum class ConnectivityMode {
    STRICT,
    RELAXED_UNCONSTRAINED
};

inline std::string to_string(ConnectivityMode mode) {
    switch (mode) {
        case ConnectivityMode::STRICT:
            return "STRICT";
        case ConnectivityMode::RELAXED_UNCONSTRAINED:
            return "RELAXED_UNCONSTRAINED";
    }
    return "UNKNOWN";
}

inline ConnectivityMode mode_from_string(const std::string& str) {
    if (str == "STRICT") {
        return ConnectivityMode::STRICT;
    } else if (str == "RELAXED" || str == "RELAXED_UNCONSTRAINED") {
        return ConnectivityMode::RELAXED_UNCONSTRAINED;
    }
    throw std::invalid_argument("Unknown connectivity mode: " + str);
}

// ============================================================================
// Exceptions
// ============================================================================

class ApxException : public std::runtime_error {
public:
    explicit ApxException(const std::string& msg) : std::runtime_error(msg) {}
};

class UnknownVertexException : public ApxException {
public:
    explicit UnknownVertexException(const std::string& msg) : ApxException(msg) {}
};

class SelfLoopException : public ApxException {
public:
    explicit SelfLoopException(const std::string& msg) : ApxException(msg) {}
};

class DuplicateEdgeInsertionException : public ApxException {
public:
    explicit DuplicateEdgeInsertionException(const std::string& msg) : ApxException(msg) {}
};

class NonexistentEdgeDeletionException : public ApxException {
public:
    explicit NonexistentEdgeDeletionException(const std::string& msg) : ApxException(msg) {}
};

class EdgeLabelMismatchDeletionException : public ApxException {
public:
    explicit EdgeLabelMismatchDeletionException(const std::string& msg) : ApxException(msg) {}
};

class ResourceLimitExceededException : public ApxException {
public:
    explicit ResourceLimitExceededException(const std::string& msg) : ApxException(msg) {}
};

class InvalidParameterException : public ApxException {
public:
    explicit InvalidParameterException(const std::string& msg) : ApxException(msg) {}
};

// ============================================================================
// Data Structs
// ============================================================================

struct DeviationScore {
    uint32_t m = 0;
    uint32_t x_in = 0;
    uint32_t x_out = 0;
    bool connected = true;

    bool operator==(const DeviationScore& o) const {
        return m == o.m && x_in == o.x_in && x_out == o.x_out && connected == o.connected;
    }

    bool operator!=(const DeviationScore& o) const {
        return !(*this == o);
    }
};

struct EvaluationResult {
    std::vector<uint32_t> mapping;
    DeviationScore score;
    bool accepted = false;
};

struct Update {
    char op = '+'; // '+' or '-'
    uint32_t u = 0;
    uint32_t v = 0;
    uint32_t label = 0;
};

struct StepDelta {
    std::map<std::vector<uint32_t>, DeviationScore> entered;
    std::map<std::vector<uint32_t>, DeviationScore> exited;
    std::map<std::vector<uint32_t>, std::pair<DeviationScore, DeviationScore>> score_changed;
    std::map<std::vector<uint32_t>, DeviationScore> unchanged;
};

inline std::string mapping_to_tuple_string(const std::vector<uint32_t>& mapping) {
    std::ostringstream oss;
    oss << "(";
    for (size_t i = 0; i < mapping.size(); ++i) {
        oss << mapping[i];
        if (i + 1 < mapping.size()) {
            oss << ", ";
        }
    }
    oss << ")";
    return oss.str();
}

} // namespace apx03

#endif // APX03_TYPES_H
