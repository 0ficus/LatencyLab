#include "latency_lab/metrics.hpp"
#include <stdexcept>
#include <iostream>
#include <dirent.h>

int fd_count() {
    DIR* dir = opendir("/proc/self/fd");
    if (!dir) throw std::runtime_error("cannot inspect descriptor lifetime");
    int count = 0;
    while (readdir(dir)) ++count;
    closedir(dir);
    return count;
}
template<class F> void requires_logic_error(F operation) {
    try { operation(); } catch (const std::logic_error&) { return; }
    throw std::runtime_error("invalid collector transition was accepted");
}
int main() {
    const int before = fd_count();
    for (int i = 0; i < 20; ++i) {
        latency_lab::MetricsCollector collector;
        requires_logic_error([&] { collector.stop(); });
        collector.start();
        requires_logic_error([&] { collector.start(); });
        const auto& metrics = collector.stop();
        if (metrics.size() != 4) throw std::runtime_error("missing metric");
        requires_logic_error([&] { collector.stop(); });
        requires_logic_error([&] { collector.start(); });
    }
    // Destruction must also close enabled descriptors without an explicit stop.
    { latency_lab::MetricsCollector collector; collector.start(); }
    if (fd_count() != before) throw std::runtime_error("file descriptor leak");
    try { latency_lab::MetricsCollector invalid(-1); return 1; }
    catch (const std::invalid_argument&) {}
    std::cout << "Collector lifecycle and descriptor cleanup: OK\n";
}
