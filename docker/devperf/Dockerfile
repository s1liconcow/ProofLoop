FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl wget git jq ripgrep unzip zip \
    build-essential cmake ninja-build pkg-config make \
    clang lldb gdb strace ltrace valgrind linux-tools-common linux-tools-generic \
    python3 python3-pip python3-venv \
    rustc cargo \
    hyperfine \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["bash"]
