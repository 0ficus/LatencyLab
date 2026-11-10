#include "arguments.hpp"
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <vector>

namespace {
using Matrix = std::vector<std::uint64_t>;
constexpr const char* usage = "demo_matrix [size=256] [passes=3] [rows|columns]";

// Row-major matrices. The inner loop reads a row of B and updates a row of C.
void rows(const Matrix& a, const Matrix& b, Matrix& c, std::size_t size) {
    for (std::size_t i = 0; i < size; ++i)
        for (std::size_t k = 0; k < size; ++k) {
            const auto value = a[i * size + k];
            for (std::size_t j = 0; j < size; ++j)
                c[i * size + j] += value * b[k * size + j];
        }
}

// The inner loop reads a column of B: adjacent reads are size elements apart.
void columns(const Matrix& a, const Matrix& b, Matrix& c, std::size_t size) {
    for (std::size_t i = 0; i < size; ++i)
        for (std::size_t j = 0; j < size; ++j) {
            std::uint64_t sum = 0;
            for (std::size_t k = 0; k < size; ++k)
                sum += a[i * size + k] * b[k * size + j];
            c[i * size + j] = sum;
        }
}
}

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--help") { std::cout << usage << '\n'; return 0; }
        if (argc > 4) throw std::invalid_argument(usage);
        const auto size = argc > 1 ? latency_lab::demos::count(argv[1], 1024, "size") : 256;
        const auto passes = argc > 2 ? latency_lab::demos::count(argv[2], 100, "passes") : 3;
        const std::string mode = argc > 3 ? argv[3] : "rows";
        if (mode != "rows" && mode != "columns") throw std::invalid_argument(usage);
        Matrix a(size * size), b(size * size), c(size * size);
        for (std::size_t i = 0; i < a.size(); ++i) {
            a[i] = (i * 3 + 1) % 17;
            b[i] = (i * 7 + 2) % 19;
        }
        std::uint64_t checksum = 0;
        for (std::size_t pass = 0; pass < passes; ++pass) {
            a[0] = (pass + 1) % 17;
            std::fill(c.begin(), c.end(), 0);
            if (mode == "rows") rows(a, b, c, size);
            else columns(a, b, c, size);
            checksum += std::accumulate(c.begin(), c.end(), std::uint64_t{0});
        }
        std::cout << "demo=matrix mode=" << mode << " order=" << (mode == "rows" ? "ikj" : "ijk")
                  << " size=" << size << " passes=" << passes << " checksum=" << checksum << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
