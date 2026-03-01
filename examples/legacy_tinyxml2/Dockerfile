FROM rust:1.80-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential cmake ninja-build python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
