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
VERSION="0.3.34"
CPU_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cpu"

have() { command -v "$1" >/dev/null 2>&1; }

# A Vulkan source build needs cmake, a C++ compiler, a shader compiler, the
# Vulkan headers, and the SPIRV-Headers CMake package. llama.cpp's ggml-vulkan
# pulls the last in via find_package(SPIRV-Headers); its config file is a
# separate package from the Vulkan loader/headers, and without it CMake fails the
# whole build. Any missing → not ready; install the CPU wheel instead.
have_vulkan_headers() {
    if have pkg-config && pkg-config --exists vulkan; then return 0; fi
    for h in /usr/include/vulkan/vulkan.h /usr/local/include/vulkan/vulkan.h; do
        [ -f "$h" ] && return 0
    done
    return 1
}

have_spirv_headers() {
    for d in /usr/lib64/cmake/SPIRV-Headers /usr/lib/cmake/SPIRV-Headers \
             /usr/share/cmake/SPIRV-Headers; do
        [ -f "$d/SPIRV-HeadersConfig.cmake" ] && return 0
    done
    return 1
}

vulkan_ready() {
    have cmake || return 1
    { have c++ || have g++; } || return 1
    { have glslc || have glslangValidator; } || return 1
    have_vulkan_headers || return 1
    have_spirv_headers || return 1
    return 0
}

install_cpu() {
    echo "=> Installing the prebuilt CPU-only llama-cpp-python wheel..."
    "$PIP" install --upgrade --extra-index-url "$CPU_INDEX" \
        "llama-cpp-python==$VERSION"
}

install_vulkan() {
    echo "=> Vulkan toolchain found — building llama-cpp-python with Vulkan..."
    # pip treats an already-installed same-version wheel as satisfied, so a CPU
    # wheel left by an earlier run (e.g. before SPIRV-Headers was installed) would
    # never be replaced by the Vulkan build. Force a rebuild only when the current
    # build lacks GPU offload, so a correct Vulkan build isn't needlessly recompiled.
    PY="$(dirname "$PIP")/python"
    force=""
    # Only force a --no-deps rebuild when llama-cpp-python is ALREADY importable
    # (its runtime deps are already present) but its current build lacks GPU
    # offload. On a fresh venv it is not installed at all, and --no-deps there
    # would skip runtime deps like diskcache and leave the import broken — so let
    # the normal install pull deps in that case.
    if "$PY" -c "import llama_cpp" 2>/dev/null && \
       ! "$PY" -c "import llama_cpp, sys; sys.exit(0 if llama_cpp.llama_supports_gpu_offload() else 1)" 2>/dev/null; then
        force="--force-reinstall --no-deps"
    fi
    # Cap build parallelism: a full -j build is itself an all-core burst.
    CMAKE_ARGS="-DGGML_VULKAN=on" CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-4}" \
        "$PIP" install --upgrade --no-cache-dir $force \
        --no-binary llama-cpp-python "llama-cpp-python==$VERSION"
}

if vulkan_ready; then
    # The `if` context suppresses `set -e`, so a failed build reaches the fallback.
    if install_vulkan; then
        exit 0
    fi
    echo "!! Vulkan build failed — falling back to the CPU-only wheel." >&2
else
    echo "=> Incomplete Vulkan toolchain (need cmake / C++ / glslc / vulkan"
    echo "   headers / SPIRV-Headers). Installing the CPU-only wheel. For GPU,"
    echo "   install those first (see README) and re-run 'make install-ai'."
fi
install_cpu
