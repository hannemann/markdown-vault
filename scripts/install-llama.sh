#!/bin/sh
# Install llama-cpp-python (the in-process "local" Ask backend) into the app venv.
#
# A Vulkan build still runs on the CPU when no layers are offloaded, so a single
# install gives the Preferences a real CPU-vs-GPU choice. This script therefore
# builds *with* the Vulkan backend when the toolchain is present, and otherwise
# installs the prebuilt CPU-only wheel. If the Vulkan source build fails, it falls
# back to the CPU wheel too, so `make install-ai` never leaves Ask uninstalled.
#
# GPU (Vulkan) support is implemented but not officially supported — see the
# README for the packages self-installers need before running `make install-ai`.
#
# Usage: install-llama.sh <path-to-venv-pip>
set -eu

PIP="${1:?usage: install-llama.sh <path-to-venv-pip>}"
VERSION="0.3.16"
CPU_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cpu"

have() { command -v "$1" >/dev/null 2>&1; }

# A Vulkan source build needs cmake, a C++ compiler, a shader compiler and the
# Vulkan headers. Any missing → not ready; install the CPU wheel instead.
vulkan_ready() {
    have cmake || return 1
    { have c++ || have g++; } || return 1
    { have glslc || have glslangValidator; } || return 1
    if have pkg-config && pkg-config --exists vulkan; then return 0; fi
    for h in /usr/include/vulkan/vulkan.h /usr/local/include/vulkan/vulkan.h; do
        [ -f "$h" ] && return 0
    done
    return 1
}

install_cpu() {
    echo "=> Installing the prebuilt CPU-only llama-cpp-python wheel..."
    "$PIP" install --upgrade --extra-index-url "$CPU_INDEX" \
        "llama-cpp-python==$VERSION"
}

install_vulkan() {
    echo "=> Vulkan toolchain found — building llama-cpp-python with Vulkan..."
    # Cap build parallelism: a full -j build is itself an all-core burst.
    CMAKE_ARGS="-DGGML_VULKAN=on" CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-4}" \
        "$PIP" install --upgrade --no-cache-dir \
        --no-binary llama-cpp-python "llama-cpp-python==$VERSION"
}

if vulkan_ready; then
    # The `if` context suppresses `set -e`, so a failed build reaches the fallback.
    if install_vulkan; then
        exit 0
    fi
    echo "!! Vulkan build failed — falling back to the CPU-only wheel." >&2
else
    echo "=> No Vulkan toolchain (cmake / C++ / glslc / vulkan headers)."
    echo "   Installing the CPU-only wheel. For GPU, install those first (see"
    echo "   README) and re-run 'make install-ai'."
fi
install_cpu
