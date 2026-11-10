#include "latency_lab/metrics.hpp"
#include <linux/perf_event.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace latency_lab {
std::string json_string(const std::string& text) {
    std::ostringstream out;
    out << '"';
    for (unsigned char c : text) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else out << c;
    }
    out << '"';
    return out.str();
}
MetricsCollector::MetricsCollector(pid_t tid) {
    if (tid < 0) throw std::invalid_argument("tid must be nonnegative; system-wide monitoring is unsupported");
    struct Event { const char* name; const char* unit; unsigned type; std::uint64_t config; };
    const Event events[] = {
        {"cycles", "count", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES},
        {"instructions", "count", PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS},
        {"cache_misses", "count", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CACHE_MISSES},
        {"task_clock", "ns", PERF_TYPE_SOFTWARE, PERF_COUNT_SW_TASK_CLOCK},
    };
    // Allocate before acquiring FDs, so allocation failure cannot leak an open FD.
    metrics_.reserve(4);
    fds_.reserve(4);
    for (const auto& event : events) {
        Metric metric;
        metric.name = event.name;
        metric.unit = event.unit;
        metrics_.push_back(metric);
        perf_event_attr attr{};
        attr.size = sizeof(attr);
        attr.type = event.type;
        attr.config = event.config;
        attr.disabled = 1;
        attr.exclude_kernel = 1;
        attr.exclude_hv = 1;
        attr.read_format = PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
        int fd = static_cast<int>(syscall(SYS_perf_event_open, &attr, tid, -1, -1, PERF_FLAG_FD_CLOEXEC));
        const int error = errno;
        fds_.push_back(fd);
        if (fd < 0) fail(metrics_.size() - 1, "perf_event_open", error);
    }
}
MetricsCollector::~MetricsCollector() {
    for (int fd : fds_) if (fd >= 0) close(fd);
}
void MetricsCollector::fail(std::size_t i, const std::string& operation, int error) {
    auto& m = metrics_[i];
    m.status = "unavailable";
    m.error_number = error;
    m.reason = operation + ": " + std::strerror(error);
    if (error == EACCES || error == EPERM)
        m.reason += "; check perf_event_paranoid, CAP_PERFMON and sandbox policy";
    if (fds_[i] >= 0) { close(fds_[i]); fds_[i] = -1; }
}
void MetricsCollector::start() {
    if (state_ != State::Ready) throw std::logic_error("start requires a fresh collector");
    for (std::size_t i = 0; i < fds_.size(); ++i) {
        if (fds_[i] < 0) continue;
        if (ioctl(fds_[i], PERF_EVENT_IOC_RESET, 0) < 0) { fail(i, "reset", errno); continue; }
        if (ioctl(fds_[i], PERF_EVENT_IOC_ENABLE, 0) < 0) fail(i, "enable", errno);
    }
    state_ = State::Running;
}
const std::vector<Metric>& MetricsCollector::stop() {
    if (state_ != State::Running) throw std::logic_error("stop requires a running collector");
    for (std::size_t i = 0; i < fds_.size(); ++i) {
        if (fds_[i] >= 0 && ioctl(fds_[i], PERF_EVENT_IOC_DISABLE, 0) < 0) fail(i, "disable", errno);
    }
    for (std::size_t i = 0; i < fds_.size(); ++i) {
        if (fds_[i] < 0) continue;
        struct { std::uint64_t value, enabled, running; } data{};
        ssize_t n;
        do { n = read(fds_[i], &data, sizeof(data)); } while (n < 0 && errno == EINTR);
        if (n != sizeof(data)) { fail(i, "read", n < 0 ? errno : EIO); continue; }
        auto& m = metrics_[i];
        m.raw_value = data.value;
        m.time_enabled_ns = data.enabled;
        m.time_running_ns = data.running;
        if (data.running == 0 || data.enabled == 0 || data.running > data.enabled) {
            m.reason = "event did not run or returned invalid timing; check PMU/CPU compatibility";
            continue;
        }
        m.value = static_cast<double>(static_cast<long double>(data.value) * data.enabled / data.running);
        m.status = "available";
    }
    state_ = State::Stopped;
    return metrics_;
}
std::string metrics_json(const std::vector<Metric>& metrics) {
    std::ostringstream out;
    out << std::setprecision(17) << '{';
    bool first = true;
    for (const auto& m : metrics) {
        if (!first) out << ',';
        first = false;
        out << json_string(m.name) << ":{\"status\":" << json_string(m.status)
            << ",\"unit\":" << json_string(m.unit) << ",\"value\":";
        if (m.status == "available") out << m.value; else out << "null";
        out << ",\"raw_value\":";
        if (m.status == "available") out << m.raw_value; else out << "null";
        out << ",\"time_enabled_ns\":" << m.time_enabled_ns
            << ",\"time_running_ns\":" << m.time_running_ns
            << ",\"errno\":" << m.error_number << ",\"reason\":";
        if (m.reason.empty()) out << "null"; else out << json_string(m.reason);
        out << '}';
    }
    out << '}';
    return out.str();
}
}
