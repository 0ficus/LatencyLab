#include <cstdint>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

int main(int argc, char** argv) {
    try {
        const int size_mb = argc > 1 ? std::stoi(argv[1]) : 32;
        const int passes = argc > 2 ? std::stoi(argv[2]) : 4;
        if (size_mb < 1 || size_mb > 1024 || passes < 1 || passes > 10000)
            throw std::invalid_argument("usage: demo_target [size_MB: 1..1024] [passes: 1..10000]");
        std::vector<std::uint64_t> data(static_cast<std::size_t>(size_mb) * 1024 * 1024 / 8);
        for (std::size_t i = 0; i < data.size(); ++i) data[i] = i + 1;
        std::uint64_t checksum = 0;
        for (int pass = 0; pass < passes; ++pass)
            for (std::size_t offset = 0; offset < 64; ++offset)
                for (std::size_t i = offset; i < data.size(); i += 64)
                    checksum += data[i];
        std::cout << "size_MB=" << size_mb << " passes=" << passes << " checksum=" << checksum << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
