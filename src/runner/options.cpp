#include "runner.hpp"
#include <sched.h>
#include <stdexcept>

namespace latency_lab::runner {
namespace {
int number(const std::string& text, int low, int high) {
    if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument("expected a nonnegative integer");
    const auto value = std::stoull(text);
    if (value < static_cast<unsigned>(low) || value > static_cast<unsigned>(high))
        throw std::invalid_argument("numeric argument is out of range");
    return static_cast<int>(value);
}
}

Options parse_options(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--help") { options.help = true; return options; }
        if (arg == "--") {
            for (++i; i < argc; ++i) options.command.emplace_back(argv[i]);
            break;
        }
        if ((arg != "--cpu" && arg != "--timeout-ms") || i + 1 >= argc)
            throw std::invalid_argument("unknown option or missing value: " + arg);
        const std::string value = argv[++i];
        if (arg == "--cpu") options.cpu = number(value, 0, CPU_SETSIZE - 1);
        else options.timeout_ms = number(value, 1, 3600000);
    }
    if (options.command.empty())
        throw std::invalid_argument("usage: runner [--cpu N] [--timeout-ms N] -- program [args...]");
    return options;
}
}
