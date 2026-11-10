#pragma once
#include <cstddef>
#include <stdexcept>
#include <string>

namespace latency_lab::demos {
inline std::size_t count(const char* text, std::size_t maximum, const char* name) {
    const std::string input(text);
    if (input.empty() || input.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument(std::string(name) + " must be a positive integer");
    const auto value = std::stoull(input);
    if (value == 0 || value > maximum)
        throw std::invalid_argument(std::string(name) + " must be in 1.." + std::to_string(maximum));
    return static_cast<std::size_t>(value);
}
}
