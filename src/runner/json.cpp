#include "runner.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <sys/utsname.h>

namespace latency_lab::runner {
namespace {
long long micros(const timeval& time) { return time.tv_sec * 1000000LL + time.tv_usec; }
std::string paranoid_setting() {
    std::ifstream file("/proc/sys/kernel/perf_event_paranoid");
    std::string value;
    std::getline(file, value);
    return value;
}
}

std::string result_json(const Result& result) {
    utsname system{};
    if (uname(&system) != 0) throw std::runtime_error("uname failed");
    std::string command;
    for (const auto& arg : result.command) { if (!command.empty()) command += ' '; command += arg; }
    const auto q = json_string;
    const auto& usage = result.resources;
    const auto& output = result.output;
    std::ostringstream json;
    json << "{\"schema_version\":1,\"mock\":false,\"command\":" << q(command)
        << ",\"status\":" << q(result.status) << ",\"error\":" << (result.error.empty() ? "null" : q(result.error))
        << ",\"cpu\":" << result.cpu << ",\"affinity_applied\":true,\"exit_code\":" << result.exit_code
        << ",\"elapsed_ns\":" << result.elapsed_ns
        << ",\"scope\":\"target_main_thread_user_space_including_exec_and_loader\""
        << ",\"kernel\":" << q(system.release) << ",\"perf_event_paranoid\":" << q(paranoid_setting())
        << ",\"resources\":{\"source\":\"wait4\",\"user_cpu_us\":" << micros(usage.ru_utime)
        << ",\"system_cpu_us\":" << micros(usage.ru_stime)
        << ",\"minor_page_faults\":" << usage.ru_minflt << ",\"major_page_faults\":" << usage.ru_majflt
        << "},\"metrics\":" << metrics_json(result.metrics)
        << ",\"stdout\":" << q(output.stdout_text) << ",\"stderr\":" << q(output.stderr_text)
        << ",\"stdout_truncated\":" << (output.stdout_truncated ? "true" : "false")
        << ",\"stderr_truncated\":" << (output.stderr_truncated ? "true" : "false") << ",\"argv\":[";
    for (std::size_t i = 0; i < result.command.size(); ++i)
        json << (i ? "," : "") << q(result.command[i]);
    json << "]}";
    return json.str();
}
}
