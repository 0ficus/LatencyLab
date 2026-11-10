#include "latency_lab/metrics.hpp"
#include <sched.h>
#include <sys/resource.h>
#include <sys/utsname.h>
#include <unistd.h>
#include <chrono>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <cerrno>
#include <cstring>

namespace {
unsigned long long number(const std::string& text) {
    if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
        throw std::invalid_argument("expected a nonnegative integer");
    return std::stoull(text);
}
std::string file_line(const char* path) {
    std::ifstream f(path);
    std::string text;
    std::getline(f, text);
    return text;
}
long long micros(const timeval& t) { return t.tv_sec * 1000000LL + t.tv_usec; }
}
int main(int argc, char** argv) {
    try {
        unsigned long long iterations = 20000000;
        int cpu = -1;
        bool require_hardware = false;
        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg == "--help") {
                std::cout << "Usage: metrics_probe [--cpu N] [--iterations N] [--require-hardware]\n"
                             "JSON on stdout; exit 2 if required hardware counters are unavailable.\n";
                return 0;
            }
            if (arg == "--require-hardware") { require_hardware = true; continue; }
            if ((arg != "--cpu" && arg != "--iterations") || i + 1 >= argc)
                throw std::invalid_argument("unknown option or missing value: " + arg);
            const auto value = number(argv[++i]);
            if (arg == "--cpu") {
                if (value >= CPU_SETSIZE) throw std::invalid_argument("CPU exceeds CPU_SETSIZE");
                cpu = static_cast<int>(value);
            } else {
                if (value == 0 || value > 1000000000ULL)
                    throw std::invalid_argument("iterations must be in 1..1000000000");
                iterations = value;
            }
        }
        cpu_set_t allowed;
        CPU_ZERO(&allowed);
        if (sched_getaffinity(0, sizeof(allowed), &allowed) != 0)
            throw std::runtime_error("sched_getaffinity failed");
        // Pin before opening events: important on hybrid P/E CPUs.
        if (cpu < 0) {
            for (int i = 0; i < CPU_SETSIZE; ++i) if (CPU_ISSET(i, &allowed)) { cpu = i; break; }
        }
        if (cpu < 0 || !CPU_ISSET(cpu, &allowed)) throw std::invalid_argument("CPU is outside allowed affinity mask");
        cpu_set_t selected;
        CPU_ZERO(&selected);
        CPU_SET(cpu, &selected);
        if (sched_setaffinity(0, sizeof(selected), &selected) != 0)
            throw std::runtime_error(std::string("sched_setaffinity: ") + std::strerror(errno));
        latency_lab::MetricsCollector collector;
        rusage before{}, after{};
        if (getrusage(RUSAGE_THREAD, &before) != 0) throw std::runtime_error("getrusage failed");
        const auto start = std::chrono::steady_clock::now();
        collector.start();
        // Unsigned recurrence is well-defined, data dependent, and emitted as checksum.
        volatile std::uint64_t accumulator = 0x123456789abcdefULL;
        for (unsigned long long i = 0; i < iterations; ++i)
            accumulator = accumulator * 6364136223846793005ULL + i + 1442695040888963407ULL;
        const auto& metrics = collector.stop();
        const auto finish = std::chrono::steady_clock::now();
        if (getrusage(RUSAGE_THREAD, &after) != 0) throw std::runtime_error("getrusage failed");
        const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(finish - start).count();
        utsname system{};
        if (uname(&system) != 0) throw std::runtime_error("uname failed");
        int available = 0;
        for (std::size_t i = 0; i < 3; ++i) if (metrics[i].status == "available") ++available;
        std::cout << "{\"schema_version\":1,\"scope\":\"calling_thread_user_space\","
            << "\"workload\":\"integer_recurrence\",\"iterations\":" << iterations
            << ",\"checksum\":" << accumulator << ",\"cpu\":" << cpu
            << ",\"kernel\":" << latency_lab::json_string(system.release)
            << ",\"effective_uid\":" << geteuid()
            << ",\"perf_event_paranoid\":" << latency_lab::json_string(file_line("/proc/sys/kernel/perf_event_paranoid"))
            << ",\"hardware_counters_available\":" << available
            << ",\"elapsed_ns\":" << elapsed
            << ",\"resources\":{\"source\":\"getrusage(RUSAGE_THREAD)\",\"user_cpu_us\":"
            << micros(after.ru_utime) - micros(before.ru_utime)
            << ",\"system_cpu_us\":" << micros(after.ru_stime) - micros(before.ru_stime)
            << ",\"minor_page_faults\":" << after.ru_minflt - before.ru_minflt
            << ",\"major_page_faults\":" << after.ru_majflt - before.ru_majflt
            << "},\"metrics\":" << latency_lab::metrics_json(metrics) << "}\n";
        return require_hardware && available != 3 ? 2 : 0;
    } catch (const std::exception& error) {
        std::cerr << "metrics_probe: " << error.what() << '\n';
        return 1;
    }
}
