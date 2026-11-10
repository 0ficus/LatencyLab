#include "arguments.hpp"
#include <cstdint>
#include <iostream>
#include <vector>

namespace {
constexpr const char* usage = "demo_binary_search [elements=1048576] [queries=200000]";

std::size_t lower_bound(const std::vector<std::uint32_t>& data, std::uint32_t key) {
    std::size_t left = 0, right = data.size();
    while (left < right) {
        const auto middle = left + (right - left) / 2;
        if (data[middle] < key) left = middle + 1;
        else right = middle;
    }
    return left;
}
}

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--help") { std::cout << usage << '\n'; return 0; }
        if (argc > 3) throw std::invalid_argument(usage);
        const auto elements = argc > 1 ? latency_lab::demos::count(argv[1], 67108864, "elements") : 1048576;
        const auto queries = argc > 2 ? latency_lab::demos::count(argv[2], 100000000, "queries") : 200000;
        std::vector<std::uint32_t> data(elements);
        for (std::size_t i = 0; i < elements; ++i) data[i] = static_cast<std::uint32_t>(2 * i);
        std::uint64_t state = 42, checksum = 0;
        std::size_t hits = 0;
        for (std::size_t query = 0; query < queries; ++query) {
            state = state * 6364136223846793005ULL + 1442695040888963407ULL;
            const auto key = static_cast<std::uint32_t>((state >> 32) % (2 * elements));
            const auto index = lower_bound(data, key);
            if (index < data.size() && data[index] == key) {
                ++hits;
                checksum += index + 1;
            }
        }
        std::cout << "demo=binary_search elements=" << elements << " queries=" << queries
                  << " seed=42 hits=" << hits << " checksum=" << checksum << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
