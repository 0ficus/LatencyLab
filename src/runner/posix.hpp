#pragma once
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>

namespace latency_lab::runner::detail {
struct Pipe {
    int read_fd = -1, write_fd = -1;
    Pipe() {
        int fds[2];
        if (pipe2(fds, O_CLOEXEC) != 0) throw std::runtime_error("pipe2 failed");
        read_fd = fds[0]; write_fd = fds[1];
    }
    Pipe(const Pipe&) = delete;
    Pipe& operator=(const Pipe&) = delete;
    ~Pipe() { close_read(); close_write(); }
    void close_read() { if (read_fd >= 0) { close(read_fd); read_fd = -1; } }
    void close_write() { if (write_fd >= 0) { close(write_fd); write_fd = -1; } }
};

struct Child {
    pid_t pid;
    bool reaped = false;
    ~Child() {
        if (pid < 0) return;
        kill(-pid, SIGKILL);
        if (!reaped) {
            kill(pid, SIGKILL);
            while (waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {}
        }
    }
    void stop_descendants() { kill(-pid, SIGKILL); pid = -1; }
};

struct ChildError { int stage, error; };
[[noreturn]] inline void child_error(int fd, int stage) {
    const ChildError error{stage, errno};
    (void)!write(fd, &error, sizeof(error));
    _exit(stage == 4 ? 127 : 126);
}
}
