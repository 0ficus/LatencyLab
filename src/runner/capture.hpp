#pragma once
#include "runner.hpp"
#include <cstdio>
#include <memory>

namespace latency_lab::runner {
// Anonymous files avoid full stdout/stderr pipes blocking the measured program.
class Capture {
public:
    Capture();
    int stdout_fd() const { return fileno(out_.get()); }
    int stderr_fd() const { return fileno(err_.get()); }
    CapturedOutput read();
private:
    struct CloseFile { void operator()(FILE* file) const { std::fclose(file); } };
    using File = std::unique_ptr<FILE, CloseFile>;
    File out_, err_;
};
}
