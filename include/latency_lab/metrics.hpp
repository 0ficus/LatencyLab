#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include <sys/types.h>

namespace latency_lab {
struct Metric {
    std::string name;
    std::string unit;
    std::string status = "unavailable";
    std::string reason;
    int error_number = 0;
    std::uint64_t raw_value = 0;
    std::uint64_t time_enabled_ns = 0;
    std::uint64_t time_running_ns = 0;
    double value = 0;
};
// One measurement interval; pid=0 measures the calling thread. No inheritance.
// For a child, pass its TID while it is blocked before exec; see docs/integration.md.
class MetricsCollector {
public:
    explicit MetricsCollector(pid_t tid = 0);
    ~MetricsCollector();
    MetricsCollector(const MetricsCollector&) = delete;
    MetricsCollector& operator=(const MetricsCollector&) = delete;
    MetricsCollector(MetricsCollector&&) = delete;
    MetricsCollector& operator=(MetricsCollector&&) = delete;
    void start();
    const std::vector<Metric>& stop();
    const std::vector<Metric>& metrics() const { return metrics_; }
private:
    void fail(std::size_t index, const std::string& operation, int error);
    std::vector<int> fds_;
    std::vector<Metric> metrics_;
    enum class State { Ready, Running, Stopped };
    State state_ = State::Ready;
};
std::string metrics_json(const std::vector<Metric>& metrics);
std::string json_string(const std::string& text);
}
