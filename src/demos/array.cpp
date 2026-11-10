#include "arguments.hpp"
#include <cstdint>
#include <iostream>
#include <vector>

namespace {
constexpr const char* usage = "demo_array [size_MB=32] [passes=8] [sequential|strided]";

std::uint64_t sequential(const std::vector<std::uint64_t>& data) {
    std::uint64_t sum = 0;
    for (std::size_t i = 0; i < data.size(); ++i) sum += data[i];
    return sum;
}

std::uint64_t strided(const std::vector<std::uint64_t>& data) {
    std::uint64_t sum = 0;
    // Visit every element once, but read elements 64 uint64_t apart before the next offset.
    for (std::size_t offset = 0; offset < 64; ++offset)
        for (std::size_t i = offset; i < data.size(); i += 64)
            sum += data[i];
    return sum;
}
}

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--help") { std::cout << usage << '\n'; return 0; }
        if (argc > 4) throw std::invalid_argument(usage);
        const auto size_mb = argc > 1 ? latency_lab::demos::count(argv[1], 512, "size_MB") : 32;
        const auto passes = argc > 2 ? latency_lab::demos::count(argv[2], 10000, "passes") : 8;
        const std::string mode = argc > 3 ? argv[3] : "sequential";
        if (mode != "sequential" && mode != "strided") throw std::invalid_argument(usage);
        std::vector<std::uint64_t> data(size_mb * 1024 * 1024 / sizeof(std::uint64_t));
        for (std::size_t i = 0; i < data.size(); ++i) data[i] = i + 1;
        std::uint64_t checksum = 0;
        for (std::size_t pass = 0; pass < passes; ++pass) {
            data[0] = pass + 1; // Keep repeated passes dependent on new input.
            checksum += mode == "sequential" ? sequential(data) : strided(data);
        }
        std::cout << "demo=array mode=" << mode << " size_MB=" << size_mb
                  << " passes=" << passes << " checksum=" << checksum << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
