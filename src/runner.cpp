#include "runner/runner.hpp"
#include <exception>
#include <iostream>

int main(int argc, char** argv) {
    try {
        const auto options = latency_lab::runner::parse_options(argc, argv);
        if (options.help) {
            std::cout << "runner [--cpu N] [--timeout-ms N] -- program [args...]\n";
            return 0;
        }
        const auto result = latency_lab::runner::measure(options);
        std::cout << latency_lab::runner::result_json(result) << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "runner: " << error.what() << '\n';
        return 1;
    }
}
