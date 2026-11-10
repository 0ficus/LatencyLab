#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "runner.hpp"
#include "capture.hpp"
#include "posix.hpp"
#include <algorithm>
#include <chrono>
#include <cstring>
#include <sched.h>
#include <sys/prctl.h>

namespace latency_lab::runner {
namespace {
using namespace detail;
using Clock = std::chrono::steady_clock;
volatile sig_atomic_t interrupted = 0;
void interrupt(int) { interrupted = 1; }

int pin_to_cpu(int cpu) {
    cpu_set_t allowed, selected;
    CPU_ZERO(&allowed);
    if (sched_getaffinity(0, sizeof(allowed), &allowed) != 0)
        throw std::runtime_error("sched_getaffinity failed");
    if (cpu < 0) for (int i = 0; i < CPU_SETSIZE; ++i)
        if (CPU_ISSET(i, &allowed)) { cpu = i; break; }
    if (cpu < 0 || !CPU_ISSET(cpu, &allowed))
        throw std::invalid_argument("CPU is outside allowed affinity mask");
    CPU_ZERO(&selected); CPU_SET(cpu, &selected);
    if (sched_setaffinity(0, sizeof(selected), &selected) != 0)
        throw std::runtime_error("sched_setaffinity: " + std::string(std::strerror(errno)));
    return cpu;
}

[[noreturn]] void execute_child(std::vector<char*>& argv, Capture& capture, Pipe& ready,
                               Pipe& gate, Pipe& errors, const sigset_t& old_mask, pid_t parent) {
    ready.close_read(); gate.close_write(); errors.close_read();
    signal(SIGINT, SIG_DFL); signal(SIGTERM, SIG_DFL); signal(SIGPIPE, SIG_DFL);
    sigprocmask(SIG_SETMASK, &old_mask, nullptr);
    if (setpgid(0, 0) != 0 || prctl(PR_SET_PDEATHSIG, SIGKILL) != 0) child_error(errors.write_fd, 1);
    if (getppid() != parent) _exit(126);
    if (dup2(capture.stdout_fd(), STDOUT_FILENO) < 0 || dup2(capture.stderr_fd(), STDERR_FILENO) < 0)
        child_error(errors.write_fd, 2);
    const int input = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (input < 0 || dup2(input, STDIN_FILENO) < 0) child_error(errors.write_fd, 2);
    if (input != STDIN_FILENO) close(input);
    const char byte = '1';
    if (write(ready.write_fd, &byte, 1) != 1) _exit(126);
    ready.close_write();
    char start;
    ssize_t n;
    do { n = read(gate.read_fd, &start, 1); } while (n < 0 && errno == EINTR);
    if (n != 1) _exit(126);
    gate.close_read();
    execvp(argv.front(), argv.data());
    child_error(errors.write_fd, 4);
}

void wait_for_target(Child& child, const sigset_t& signals, Clock::time_point start,
                     int timeout_ms, Result& result) {
    bool killed = false;
    int status = 0;
    for (;;) {
        const auto waited = wait4(child.pid, &status, WNOHANG, &result.resources);
        if (waited == child.pid) { child.reaped = true; break; }
        if (waited < 0 && errno != EINTR) throw std::runtime_error("wait4 failed");
        const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count();
        if (!killed && (interrupted || elapsed >= static_cast<long long>(timeout_ms) * 1000000)) {
            result.status = interrupted ? "cancelled" : "timeout";
            kill(-child.pid, SIGKILL);
            killed = true;
        }
        const long long remaining = killed ? 100000000LL :
            std::max(1LL, static_cast<long long>(timeout_ms) * 1000000 - elapsed);
        timespec wait_time{static_cast<time_t>(remaining / 1000000000), static_cast<long>(remaining % 1000000000)};
        (void)sigtimedwait(&signals, nullptr, &wait_time);
    }
    result.elapsed_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count();
    result.exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    if (result.status == "ok" && result.exit_code != 0) {
        result.status = "target_error";
        result.error = "Target returned a non-zero exit code or was terminated by a signal";
    }
}
}

Result measure(const Options& options) {
    Result result;
    result.command = options.command;
    // The short-lived runner and its child share one CPU; the Python TUI is unaffected.
    result.cpu = pin_to_cpu(options.cpu);
    Capture capture;
    Pipe ready, gate, errors;
    sigset_t child_signals, old_mask;
    sigemptyset(&child_signals); sigaddset(&child_signals, SIGCHLD);
    if (sigprocmask(SIG_BLOCK, &child_signals, &old_mask) != 0)
        throw std::runtime_error("cannot block SIGCHLD");
    struct sigaction action{};
    action.sa_handler = interrupt;
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, nullptr); sigaction(SIGTERM, &action, nullptr);
    signal(SIGPIPE, SIG_IGN);
    std::vector<char*> argv;
    for (const auto& arg : options.command) argv.push_back(const_cast<char*>(arg.c_str()));
    argv.push_back(nullptr);
    const pid_t parent = getpid(), pid = fork();
    if (pid < 0) throw std::runtime_error("fork failed");
    if (pid == 0) execute_child(argv, capture, ready, gate, errors, old_mask, parent);
    Child child{pid};
    (void)setpgid(pid, pid);
    ready.close_write(); gate.close_read(); errors.close_write();
    char byte;
    ssize_t n;
    do { n = read(ready.read_fd, &byte, 1); } while (n < 0 && errno == EINTR && !interrupted);
    ready.close_read();
    if (n != 1) throw std::runtime_error("target setup failed or launch was cancelled");
    MetricsCollector collector(pid);
    collector.start();
    const auto start = Clock::now();
    if (write(gate.write_fd, &byte, 1) != 1) throw std::runtime_error("cannot release target");
    gate.close_write();
    wait_for_target(child, child_signals, start, options.timeout_ms, result);
    result.metrics = collector.stop();
    child.stop_descendants();
    ChildError failure{};
    if (read(errors.read_fd, &failure, sizeof(failure)) == sizeof(failure)) {
        result.status = "launch_error";
        result.error = "execvp: " + std::string(std::strerror(failure.error));
    }
    sigprocmask(SIG_SETMASK, &old_mask, nullptr);
    result.output = capture.read();
    return result;
}
}
