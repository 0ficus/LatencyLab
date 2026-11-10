#pragma once
#include "latency_lab/metrics.hpp"
#include <string>
#include <sys/resource.h>
#include <vector>

namespace latency_lab::runner {
struct Options {
    int cpu = -1;
    int timeout_ms = 10000;
    bool help = false;
    std::vector<std::string> command;
};

struct CapturedOutput {
    std::string stdout_text, stderr_text;
    bool stdout_truncated = false, stderr_truncated = false;
};

struct Result {
    std::vector<std::string> command;
    int cpu = -1, exit_code = 0;
    long long elapsed_ns = 0;
    std::string status = "ok", error;
    rusage resources{};
    std::vector<Metric> metrics;
    CapturedOutput output;
};

Options parse_options(int argc, char** argv);
Result measure(const Options& options);
std::string result_json(const Result& result);
}
