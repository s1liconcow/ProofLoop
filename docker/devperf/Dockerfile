FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl wget git jq ripgrep unzip zip \
    build-essential cmake ninja-build pkg-config make \
    clang lldb gdb strace ltrace valgrind linux-tools-common linux-tools-generic \
    python3 python3-pip python3-venv \
    rustc cargo \
    hyperfine \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for Codex/Claude CLIs)
RUN mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install coding agent CLIs
# - Codex: https://github.com/openai/codex
# - Claude Code: https://docs.anthropic.com/en/docs/claude-code/getting-started
# - OpenCode: https://github.com/sst/opencode
# - Gemini CLI: https://github.com/google-gemini/gemini-cli
RUN npm install -g @openai/codex @anthropic-ai/claude-code @google/gemini-cli \
    && curl -fsSL https://opencode.ai/install | bash \
    && ln -sf /root/.opencode/bin/opencode /usr/local/bin/opencode \
    && codex --version \
    && claude --version \
    && gemini --version \
    && opencode --version

WORKDIR /workspace

CMD ["bash"]
