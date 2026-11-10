#include "capture.hpp"
#include <algorithm>
#include <fcntl.h>
#include <stdexcept>

namespace latency_lab::runner {
namespace {
constexpr std::size_t output_cap = 64 * 1024;
std::string read_file(FILE* file, bool& truncated) {
    std::rewind(file);
    std::string data(output_cap + 1, '\0');
    const auto count = std::fread(data.data(), 1, data.size(), file);
    if (std::ferror(file)) throw std::runtime_error("cannot read target output");
    truncated = count > output_cap;
    data.resize(std::min(count, output_cap));
    return data;
}
}

Capture::Capture() : out_(std::tmpfile()), err_(std::tmpfile()) {
    if (!out_ || !err_) throw std::runtime_error("cannot create output capture files");
    if (fcntl(stdout_fd(), F_SETFD, FD_CLOEXEC) < 0 || fcntl(stderr_fd(), F_SETFD, FD_CLOEXEC) < 0)
        throw std::runtime_error("cannot configure output capture");
}

CapturedOutput Capture::read() {
    CapturedOutput output;
    output.stdout_text = read_file(out_.get(), output.stdout_truncated);
    output.stderr_text = read_file(err_.get(), output.stderr_truncated);
    return output;
}
}
