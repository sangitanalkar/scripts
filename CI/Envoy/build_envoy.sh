#!/bin/bash
# © Copyright IBM Corporation 2026
# LICENSE: Apache License, Version 2.0 (http://www.apache.org/licenses/LICENSE-2.0)
# Builds Envoy (main branch) on s390x.
#
# To run on a local VM:
#   env LOZ_ENVOY_CI_LOCAL_TEST="true" bash build_envoy.sh

set -ex

# To run this script on a local vm:
#   env LOZ_ENVOY_CI_LOCAL_TEST="true" bash <path to script dir>/build_envoy.sh
: ${LOZ_ENVOY_CI_LOCAL_TEST:="false"}

cat /etc/os-release
gcc -v 2>&1 | head -3 || true
ls

sudo rm -rf "$SOURCE_ROOT/build_bazel.sh"* "$SOURCE_ROOT/logs"
sudo rm -rf .cache "$SOURCE_ROOT/.cache" /root/.cache
sudo rm -rf "$SOURCE_ROOT/gcc_build" "$SOURCE_ROOT/gcc-11.4.0"
sudo rm -rf "$SOURCE_ROOT/rules_foreign_cc"
sudo rm -rf "$SOURCE_ROOT/rules_rust"
sudo rm -rf "$SOURCE_ROOT/.rustup" "$SOURCE_ROOT/.cargo"
sudo rm -rf /root/.rustup /root/.cargo
sudo rm -rf "$SOURCE_ROOT/bazel/"
ls

SOURCE_ROOT="$(pwd)"
CLANG_VERSION="22.1.8"
BAZEL_VERSION="8.8.1"
GO_VERSION="1.25.0"
LLVM_HOME_DIR="${SOURCE_ROOT}/clang/LLVM-${CLANG_VERSION}-Linux"
PATCH_URL="https://raw.githubusercontent.com/sangitanalkar/scripts/envoy-ci-test/CI/Envoy/patch"


# Pinned bazel-registry commit that matches the dependency versions used below.
# Do not update this commit independently of the dep versions — they must stay in sync.
ENVOY_REGISTRY="https://raw.githubusercontent.com/envoyproxy/bazel-registry/20dc7c2578669f36e7c18510e65c7178cf5bb304"

export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-s390x
export PATH=$JAVA_HOME/bin:$PATH

if [[ $LOZ_ENVOY_CI_LOCAL_TEST != "true" ]]; then
  export HOME=${SOURCE_ROOT}
  export XDG_CACHE_HOME=${SOURCE_ROOT}
fi

msg()    { echo "${*}"; }
msglog() { echo "${*}"; }

# ===========================================================================
# configureAndInstall — top-level orchestrator
# ===========================================================================
configureAndInstall() {

  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y autoconf curl wget git libtool patch python3-pip \
    unzip zip pkg-config locales libssl-dev build-essential openjdk-21-jdk-headless \
    python3 python3-dev libtinfo5

  buildAndInstallClang
  buildAndInstallBazel
  installGo
  installRust

  cd "$SOURCE_ROOT"
  if [[ $LOZ_ENVOY_CI_LOCAL_TEST == "true" && ! -d envoy ]]; then
    echo "Local test: cloning the envoy repo"
    retry git clone --depth 1 -b main https://github.com/envoyproxy/envoy.git
  fi

  cd "$SOURCE_ROOT/envoy"
  setupBazelEnvironment

  installEnvoyBuildPatch

  export CARGO_HOME="${HOME}/.cargo"
  cd "$SOURCE_ROOT/envoy"
  rm -f .cargo/config.toml
  rmdir .cargo 2>/dev/null || true

  # cargo-bazel (using crates-index 3.7.0) looks for crate registry index config.json
  # at multiple paths depending on HashKind (Stable vs Legacy) and URL.
  # Empirically (confirmed via strace) the failing path is always
  #   ${CARGO_HOME}/registry/index/index.crates.io-7f555b6b8ccf4919/config.json
  # We pre-create all known variant hash directories with config.json.
  _crates_io_cfg() {
    local d="${CARGO_HOME}/registry/index/${1}"
    if [[ ! -f "${d}/config.json" ]]; then
      mkdir -p "${d}"
      cat > "${d}/config.json" << 'SPCFG_EOF'
{
  "dl": "https://static.crates.io/crates",
  "api": "https://crates.io"
}
SPCFG_EOF
      msg "crate index dir pre-created: ${d}"
    fi
  }
  # 1949cf8c6b5b557f — legacy SipHash of sparse+https://index.crates.io/ (cargo-created)
  # 6f17d22bba15001f — legacy SipHash of https://index.crates.io/
  # 9a317a96d82ef565 — sha256[0:8]-LE of sparse+https://index.crates.io/
  # 7f555b6b8ccf4919 — stable SipHash used by crates-index 3.7.0 for TreeResolver workspace
  _crates_io_cfg "index.crates.io-1949cf8c6b5b557f"
  _crates_io_cfg "index.crates.io-6f17d22bba15001f"
  _crates_io_cfg "index.crates.io-9a317a96d82ef565"
  _crates_io_cfg "index.crates.io-7f555b6b8ccf4919"

  # Common bazel flags
  _BAZEL_FLAGS=(
    -c opt --config=libc++
    --lockfile_mode=off
    --cxxopt=-std=c++20 --host_cxxopt=-std=c++20
    --repo_env=CARGO_HOME="${CARGO_HOME}"
    --repo_env=CARGO_BAZEL_ISOLATED=false
    --repo_env=CARGO_BAZEL_REPIN=1
  )

  cd "$SOURCE_ROOT/envoy" || { echo "ERROR: envoy dir missing for bazel build"; exit 1; }

  # jinja2 must be installed into the hermetic Python 3.12 that Bazel extracts
  # under the output base. The genrule replaces PATH with that Python, so system
  # site-packages is unreachable regardless of PYTHONPATH.
  # Pre-install if already on disk (warm cache). On cold cache, pass 1 extracts
  # the hermetic Python, we install into it, pass 2 succeeds via action cache.
  _jinja2_install() {
    local _py
    _py="$(bazel info output_base 2>/dev/null)/external/rules_python++python+python_3_12_s390x-unknown-linux-gnu/bin/python3"
    [[ -x "$_py" ]] || return 0
    "$_py" -m pip install --quiet jinja2==3.0.3 markupsafe==2.1.5
    msg "jinja2: installed into hermetic Python3.12"
  }

  _jinja2_install

  set +e
  bazel build //source/exe:envoy-static "${_BAZEL_FLAGS[@]}"
  _rc=$?
  set -e

  if [[ $_rc -ne 0 ]]; then
    _jinja2_install
    msg "jinja2 installed — retrying build"
    bazel build //source/exe:envoy-static "${_BAZEL_FLAGS[@]}"
  fi

  msglog "Build complete."
  msglog "Binary: ${SOURCE_ROOT}/envoy/bazel-bin/source/exe/envoy-static"
}

# ===========================================================================
# buildAndInstallClang
# Builds clang/LLVM 22.1.8 from source if not already present.
# ===========================================================================
buildAndInstallClang() {
  cd "$SOURCE_ROOT"
  if [[ -d "clang/LLVM-${CLANG_VERSION}-Linux" ]]; then
    echo "Using existing ${SOURCE_ROOT}/clang/LLVM-${CLANG_VERSION}-Linux clang distribution"
    return 0
  fi

  rm -rf clang
  local z_default_arch="z13"
  sudo apt-get update
  sudo apt-get install -y g++ curl git cmake ninja-build chrpath libelf-dev libffi-dev patchutils xz-utils python3 \
    libedit-dev libncurses-dev binutils-dev libxml2-dev libjsoncpp-dev pkg-config procps zlib1g-dev libzstd-dev libpfm4-dev

  rm -rf "$SOURCE_ROOT/clang-build"
  mkdir -p "$SOURCE_ROOT/clang-build"
  cd "$SOURCE_ROOT/clang-build"
  curl -sSL "${PATCH_URL}/Release-s390x.cmake" -o Release-s390x.cmake
  git clone -b "llvmorg-$CLANG_VERSION" --depth=1 https://github.com/llvm/llvm-project.git  || { echo "Could not clone the llvm repo. Exiting..."; exit 1; }
  cd llvm-project
  sed -i 's/set_final_stage_var(CLANG_BOLT "INSTRUMENT" STRING)/set_final_stage_var(CLANG_BOLT "OFF" STRING)/' clang/cmake/caches/Release.cmake

  cd ../
  mkdir build

  env CC="gcc" CXX="g++" \
    cmake -G "Ninja" -B build -S llvm-project/llvm \
        -DLLVM_RELEASE_ENABLE_LTO="OFF" \
        -DLLVM_PARALLEL_LINK_JOBS=4 \
        -DBOOTSTRAP_LLVM_PARALLEL_LINK_JOBS=4 \
        -DLLVM_RELEASE_ENABLE_RUNTIMES="compiler-rt;libcxx;libcxxabi" \
        -DLLVM_RELEASE_ENABLE_PROJECTS="clang;lld;clang-tools-extra" \
        -DLLVM_RELEASE_CLANG_SYSTEMZ_DEFAULT_ARCH="$z_default_arch" \
        -C llvm-project/clang/cmake/caches/Release.cmake \
        -C Release-s390x.cmake

  ninja -C build stage2-package

  mkdir -p "$SOURCE_ROOT"/clang
  cd "$SOURCE_ROOT"/clang
  tar xf "${SOURCE_ROOT}/clang-build/build/tools/clang/stage2-instrumented-bins/tools/clang/stage2-bins/LLVM-${CLANG_VERSION}-Linux.tar.xz"
  cd "$SOURCE_ROOT"/
  rm -rf "${SOURCE_ROOT}/clang-build"
}

buildAndInstallBazel() {
  cd "$SOURCE_ROOT"
  local bazel_build_dir="bazel-build/${BAZEL_VERSION}/"
  if [[ -f "${bazel_build_dir}/output/bazel" ]]; then
    sudo cp ${bazel_build_dir}/output/bazel /usr/local/bin/
    echo "Using existing ${SOURCE_ROOT}/${BAZEL_VERSION}/bazel bazel distribution"
    return 0
  fi

  rm -rf "bazel-build"
  mkdir -p "${bazel_build_dir}/"
  cd "${bazel_build_dir}/"
  wget -q https://github.com/bazelbuild/bazel/releases/download/${BAZEL_VERSION}/bazel-${BAZEL_VERSION}-dist.zip
  unzip -q bazel-${BAZEL_VERSION}-dist.zip
  chmod -R +w .
  env EXTRA_BAZEL_ARGS="--tool_java_runtime_version=local_jdk" BAZEL_DEV_VERSION_OVERRIDE="$BAZEL_VERSION" bash ./compile.sh
  sudo cp output/bazel /usr/local/bin/
  cd "$SOURCE_ROOT"
}

# ===========================================================================
# installGo
# ===========================================================================
installGo() {
  cd "$SOURCE_ROOT"
  wget -q "https://golang.org/dl/go${GO_VERSION}.linux-s390x.tar.gz"
  chmod ugo+r "go${GO_VERSION}.linux-s390x.tar.gz"
  sudo rm -rf /usr/local/go
  sudo tar -C /usr/local -xzf "go${GO_VERSION}.linux-s390x.tar.gz"
  rm -f "go${GO_VERSION}.linux-s390x.tar.gz"
  export PATH=/usr/local/go/bin:$PATH
  go version
  cd "$SOURCE_ROOT"
}

# ===========================================================================
# installRust
# ===========================================================================
installRust() {
  cd "$SOURCE_ROOT"
  # Remove any existing rustup/cargo to avoid "missing manifest" errors
  # from a partial or stale toolchain left by a previous run.
  rm -rf "$HOME/.rustup" "$HOME/.cargo"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh /dev/stdin -y
  export PATH=$HOME/.cargo/bin:$PATH
  rustc --version
  cargo --version
  cd "$SOURCE_ROOT"
}

# ===========================================================================
# setupBazelEnvironment
# Appends s390x-specific knobs to user.bazelrc.  Called before
# installEnvoyBuildPatch so that the file exists when patches are applied.
# user.bazelrc is git-ignored and therefore survives git clean.
# ===========================================================================
setupBazelEnvironment() {
  # Disable heap checking — gperftools has no s390x support and causes timeouts
  echo "build --test_env=HEAPCHECK=" >> "${SOURCE_ROOT}/envoy/user.bazelrc"
  echo "test  --test_env=HEAPCHECK=" >> "${SOURCE_ROOT}/envoy/user.bazelrc"
  # cargo-bazel must not run in isolated mode on s390x (no pre-built binary)
  echo "build --repo_env=CARGO_BAZEL_ISOLATED=false" >> "${SOURCE_ROOT}/envoy/user.bazelrc"
}

# ===========================================================================
# Inline patch writers (re-written after every git clean)
# ===========================================================================

installBoringsslPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  rm -f "$SOURCE_ROOT/envoy/bazel/boringssl-s390x.patch"
  curl -sSL "${PATCH_URL}/boringssl-s390x.patch" -o $SOURCE_ROOT/envoy/bazel/boringssl-s390x.patch
  msg "boringssl-s390x.patch: written"
}

installRulesRustPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  # Two-file patch:
  #  1. generate_utils.bzl: add CARGO_HOME to CRATES_REPOSITORY_ENVIRON so
  #     --repo_env=CARGO_HOME is visible inside the module extension context.
  #  2. common_utils.bzl: when isolated=False, explicitly return CARGO_HOME
  #     in the env dict passed to repository_ctx.execute() (which replaces
  #     the subprocess env entirely — cargo-bazel would otherwise have no HOME).
  rm -f "$SOURCE_ROOT/envoy/bazel/rules_rust-s390x.patch"
  curl -sSL "${PATCH_URL}/rules_rust-s390x.patch" -o $SOURCE_ROOT/envoy/bazel/rules_rust-s390x.patch
  msg "rules_rust-s390x.patch: written (generate_utils.bzl + common_utils.bzl)"
}

installLuajitAsPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  rm -f "$SOURCE_ROOT/envoy/bazel/luajit-as.patch"
  curl -sSL "${PATCH_URL}/luajit-as.patch" -o $SOURCE_ROOT/envoy/bazel/luajit-as.patch
  msg "luajit-as.patch: written"
}

installToolchainsLlvmPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  rm -f "$SOURCE_ROOT/envoy/bazel/toolchains_llvm-s390x.patch"
  curl -sSL "${PATCH_URL}/toolchains_llvm-s390x.patch" -o $SOURCE_ROOT/envoy/bazel/toolchains_llvm-s390x.patch
  msg "toolchains_llvm-s390x.patch: written"
}

installGrpcS390xPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  rm -f "$SOURCE_ROOT/envoy/bazel/grpc-s390x.patch"
  curl -sSL "${PATCH_URL}/grpc-s390x.patch" -o $SOURCE_ROOT/envoy/bazel/grpc-s390x.patch
  msg "grpc-s390x.patch: written"
}

installProtobufS390xPatch() {
  # protobuf 35.1 no longer uses the protoc alias block that needed s390x patching
  msg "protobuf-s390x.patch: not needed for 35.1"
}

installQuicheS390xPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel/external"
  rm -f "$SOURCE_ROOT/envoy/bazel/external/quiche-s390x.patch"
  curl -sSL "${PATCH_URL}/quiche-s390x.patch" -o $SOURCE_ROOT/envoy/bazel/external/quiche-s390x.patch
  msg "quiche-s390x.patch: written"
}

installV8S390xPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  curl -fsSL \
    "https://raw.githubusercontent.com/linux-on-ibm-z/scripts/master/Envoy/1.39.0/patch/v8-s390x.patch" \
    -o "$SOURCE_ROOT/envoy/bazel/v8-s390x.patch"
  msg "v8-s390x.patch: fetched"
}

installV8BzlmodPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  rm -f "$SOURCE_ROOT/envoy/bazel/v8-bzlmod.patch"
  curl -sSL "${PATCH_URL}/v8-bzlmod.patch" -o $SOURCE_ROOT/envoy/bazel/v8-bzlmod.patch
  msg "v8-bzlmod.patch: written"
}

installHighwayS390xPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  curl -fsSL \
    "https://raw.githubusercontent.com/linux-on-ibm-z/scripts/master/Envoy/1.39.0/patch/highway-s390x.patch" \
    -o "$SOURCE_ROOT/envoy/bazel/highway-s390x.patch"
  msg "highway-s390x.patch: fetched"
}

installProxyWasmCppHostS390xPatch() {
  mkdir -p "$SOURCE_ROOT/envoy/bazel"
  curl -fsSL \
    "https://raw.githubusercontent.com/linux-on-ibm-z/scripts/master/Envoy/1.39.0/patch/proxy_wasm_cpp_host-s390x.patch" \
    -o "$SOURCE_ROOT/envoy/bazel/proxy-wasm-cpp-host-s390x.patch"
  msg "proxy-wasm-cpp-host-s390x.patch: fetched"
}

installV8NoPipPatch() {
  # v8-nopip.patch is generated by runModulePatch (write_v8_nopip_patch function).
  # No manual patch content here — the file is created in the runModulePatch step.
  msg "v8-nopip.patch: will be written by runModulePatch"
}

# ===========================================================================
# installEnvoyBuildPatch
# Orchestrates all patch writes then applies Cargo.Bazel.lock and MODULE.bazel
# transformations.
# ===========================================================================
installEnvoyBuildPatch() {
  cd "$SOURCE_ROOT/envoy"

  # Allow git to operate even if the repo owner differs from the running user
  # (common in Docker containers where files are owned by a different uid).
  git config --global --add safe.directory "$SOURCE_ROOT/envoy" 2>/dev/null || true

  # Hard-reset any state left by a previous failed run
  git reset --hard HEAD 2>/dev/null || true
  git clean -fd bazel/ 2>/dev/null || true

  # Re-write patch files (git clean removes untracked files)
  installBoringsslPatch
  installRulesRustPatch
  installLuajitAsPatch
  installToolchainsLlvmPatch
  installGrpcS390xPatch
  installProtobufS390xPatch
  installQuicheS390xPatch
  installHighwayS390xPatch
  installProxyWasmCppHostS390xPatch
  installV8S390xPatch
  installV8BzlmodPatch
  installV8NoPipPatch

  # ---- 1. Cargo.Bazel.lock: mirror aarch64 entries as s390x ----
  runCargoBazelLockPatch

  # ---- 2a. bazel/envoy_binary.bzl: add -latomic after -pthread ----
  if ! grep -q '"-latomic"' bazel/envoy_binary.bzl; then
    sed -i 's/            "-pthread",/            "-pthread",\n            "-latomic",/' \
      bazel/envoy_binary.bzl
    msg "bazel/envoy_binary.bzl: patched (-latomic)"
  else
    msg "bazel/envoy_binary.bzl: already patched"
  fi

  # ---- 2b. bazel/platforms/BUILD: add linux_s390x platform ----
  if ! grep -q 'linux_s390x' bazel/platforms/BUILD; then
    cat >> bazel/platforms/BUILD << 'PLATFORMS_EOF'

platform(
    name = "linux_s390x",
    constraint_values = [
        "@platforms//os:linux",
        "@platforms//cpu:s390x",
    ],
)
PLATFORMS_EOF
    msg "bazel/platforms/BUILD: patched (linux_s390x)"
  else
    msg "bazel/platforms/BUILD: already patched"
  fi

  # ---- 2c. bazel/extensions.bzl: add s390x to arch_alias dict ----
  if ! grep -q '"s390x"' bazel/extensions.bzl; then
    sed -i 's|"aarch64": str(Label("//bazel/platforms/rbe:linux_arm64")),|"aarch64": str(Label("//bazel/platforms/rbe:linux_arm64")),\n            "s390x": str(Label("//bazel/platforms:linux_s390x")),|' \
      bazel/extensions.bzl
    msg "bazel/extensions.bzl: patched (s390x arch_alias)"
  else
    msg "bazel/extensions.bzl: already patched"
  fi

  # ---- 3. MODULE.bazel: archive_overrides + llvm_s390x toolchain ----
  runModulePatch

  cd "$SOURCE_ROOT"
}

# ===========================================================================
# runCargoBazelLockPatch
# Mirrors every aarch64-unknown-linux-gnu entry in Cargo.Bazel.lock as
# s390x-unknown-linux-gnu, injects s390x into the conditions map, and clears
# the checksum so Bazel accepts the modified file.
# ===========================================================================
runCargoBazelLockPatch() {
  local cargo_lock_patch="$SOURCE_ROOT/cargo_lock_patch.py"
  curl -sSfL "${PATCH_URL}/cargo_lock_patch.py" -o "$cargo_lock_patch"
  ENVOY_DIR="$SOURCE_ROOT/envoy" python3 "$cargo_lock_patch"
}

# ===========================================================================
# runModulePatch
# Fetches upstream patches and overlay files, generates all archive_override
# stanzas in MODULE.bazel, injects the llvm_toolchain_s390x extension, patches
# luajit BUILD.bazel for s390x, and generates v8-nopip.patch.
# Requires: ENVOY_DIR, LLVM_HOME_DIR, ENVOY_REGISTRY in the environment.
# ===========================================================================
runModulePatch() {
  local module_patch="$SOURCE_ROOT/module_patch.py"
  curl -sSfL "${PATCH_URL}/module_patch.py" -o "$module_patch"
  ENVOY_DIR="$SOURCE_ROOT/envoy" \
  LLVM_HOME_DIR="$LLVM_HOME_DIR" \
  ENVOY_REGISTRY="$ENVOY_REGISTRY" \
    python3 "$module_patch"
}

# ===========================================================================
# retry — retry a command up to 5 times with a 3-second back-off
# ===========================================================================
retry() {
  local max_retries=5
  local retry=0
  until "$@"; do
    exit=$?
    wait=3
    retry=$((retry + 1))
    if [[ $retry -lt $max_retries ]]; then
      echo "Retry $retry/$max_retries exited $exit, retrying in $wait seconds..."
      sleep $wait
    else
      echo "Retry $retry/$max_retries exited $exit, no more retries left."
      return $exit
    fi
  done
  return 0
}

# ======================================================
# Start of commands
configureAndInstall
