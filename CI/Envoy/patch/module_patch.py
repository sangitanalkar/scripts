import os, sys, re, subprocess

llvm_path = os.environ["LLVM_HOME_DIR"]
envoy_reg = os.environ["ENVOY_REGISTRY"]
bcr       = "https://bcr.bazel.build"
bdir      = "bazel"
os.chdir(os.environ.get("ENVOY_DIR", "."))

def fetch(url, dest):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    r = subprocess.run(["curl", "-sSfL", "-o", dest, url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("ERROR fetching " + url + ": " + r.stderr, file=sys.stderr)
        sys.exit(1)
    print("  fetched " + dest)

def fetch_text(url):
    r = subprocess.run(["curl", "-sSfL", url],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("ERROR fetching " + url, file=sys.stderr)
        sys.exit(1)
    return r.stdout

def fetch_patch(url, dest):
    raw = fetch_text(url)
    idx = raw.find("\ndiff --git ")
    if idx == -1:
        idx = raw.find("diff --git ")
        if idx == -1:
            print("ERROR: no diff --git in " + url, file=sys.stderr)
            sys.exit(1)
    else:
        idx += 1
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w") as f:
        f.write(raw[idx:])
    print("  wrote patch: " + dest)

def add_file_patch(dest, content, name):
    lines = [l + "\n" for l in content.splitlines()]
    body  = "".join("+" + l for l in lines)
    patch = ("diff --git a/" + name + " b/" + name + "\n"
             "new file mode 100644\n"
             "--- /dev/null\n"
             "+++ b/" + name + "\n"
             "@@ -0,0 +1," + str(len(lines)) + " @@\n"
             + body)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w") as f:
        f.write(patch)
    print("  wrote add-file patch: " + dest)

def replace_file_patch(dest, old, new, name):
    old_lines = [l + "\n" for l in old.splitlines()]
    new_lines = [l + "\n" for l in new.splitlines()]
    patch = ("diff --git a/" + name + " b/" + name + "\n"
             "--- a/" + name + "\n"
             "+++ b/" + name + "\n"
             "@@ -1," + str(len(old_lines)) + " +1," + str(len(new_lines)) + " @@\n"
             + "".join("-" + l for l in old_lines)
             + "".join("+" + l for l in new_lines))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w") as f:
        f.write(patch)
    print("  wrote replace-file patch: " + dest)

def blabel(p):
    p = p.replace("\\", "/")
    if p.startswith("bazel/external/"):
        return "//bazel/external:" + os.path.basename(p)
    return "//bazel:" + os.path.basename(p)

ER = envoy_reg

deps = [
    {
        "name": "boringssl",
        "url": "https://github.com/google/boringssl/releases/download/0.20260813.0/boringssl-0.20260813.0.tar.gz",
        "integrity": "sha256-N+I8uaX6VPAbB8rdZTzrwdGyNZRUOabDNP6ljqR7Wwo=",
        "strip": "boringssl-0.20260813.0/",
        "pstrip": 1,
        "patches": [],
        "overlays": [],
        "s390x": bdir + "/boringssl-s390x.patch",
    },
    {
        "name": "boringssl-source",
        "url": "https://github.com/google/boringssl/releases/download/0.20260813.0/boringssl-0.20260813.0.tar.gz",
        "integrity": "sha256-N+I8uaX6VPAbB8rdZTzrwdGyNZRUOabDNP6ljqR7Wwo=",
        "strip": "boringssl-0.20260813.0/",
        "pstrip": 1,
        "patches": [],
        "overlays": [
            (ER + "/modules/boringssl-source/0.20260813.0.envoy/overlay/BUILD.bazel",
             bdir + "/boringssl-source-overlay-BUILD.patch", "BUILD.bazel", None),
            (ER + "/modules/boringssl-source/0.20260813.0.envoy/MODULE.bazel",
             bdir + "/boringssl-source-overlay-MODULE.patch", "MODULE.bazel",
             "https://raw.githubusercontent.com/google/boringssl/refs/tags/0.20260813.0/MODULE.bazel"),
        ],
        "s390x": None,
    },
    {
        "name": "quiche",
        "url": "https://github.com/google/quiche/archive/7f07dc4d14c5702607a0dee9c7e4ab07f63f9883.tar.gz",
        "integrity": "sha256-YI8/Lnv4lmm/77LsbsV/BfSFy/TpGI7Z5gDeAGYqo8Q=",
        "strip": "quiche-7f07dc4d14c5702607a0dee9c7e4ab07f63f9883",
        "pstrip": 1,
        "patches": [
            (ER, "modules/quiche/0.0.0-260922-7f07dc4.envoy/patches/delete-bazel-files.patch",
             bdir + "/external/delete-bazel-files.patch"),
            (ER, "modules/quiche/0.0.0-260922-7f07dc4.envoy/patches/balsa_frame_unused_variable.patch",
             bdir + "/external/balsa_frame_unused_variable.patch"),
        ],
        "overlays": [
            (ER + "/modules/quiche/0.0.0-260922-7f07dc4.envoy/overlay/BUILD.bazel",
             bdir + "/external/quiche-overlay-BUILD.patch", "BUILD.bazel", None),
            (ER + "/modules/quiche/0.0.0-260922-7f07dc4.envoy/overlay/quiche_overlay.bzl",
             bdir + "/external/quiche-overlay-quiche_overlay_bzl.patch", "quiche_overlay.bzl", None),
            (ER + "/modules/quiche/0.0.0-260922-7f07dc4.envoy/overlay/extensions.bzl",
             bdir + "/external/quiche-overlay-extensions_bzl.patch", "extensions.bzl", None),
            (ER + "/modules/quiche/0.0.0-260922-7f07dc4.envoy/overlay/envoy_deps.bzl",
             bdir + "/external/quiche-overlay-envoy_deps_bzl.patch", "envoy_deps.bzl", None),
            # Replace upstream MODULE.bazel (missing @platforms dep and
            # extensions.bzl use_extension) with the registry overlay.
            (ER + "/modules/quiche/0.0.0-260922-7f07dc4.envoy/MODULE.bazel",
             bdir + "/external/quiche-overlay-MODULE.patch", "MODULE.bazel",
             "https://raw.githubusercontent.com/google/quiche/7f07dc4d14c5702607a0dee9c7e4ab07f63f9883/MODULE.bazel"),
        ],
        "s390x": bdir + "/external/quiche-s390x.patch",
    },
    {
        "name": "proxy-wasm-cpp-host",
        "url": "https://github.com/proxy-wasm/proxy-wasm-cpp-host/archive/f2db56af443571e92a31c0b877106d9ea96e19ef.tar.gz",
        "integrity": "sha256-NNrFvOvwsVbkNb+N2b2sW+YLlflnxCDGgFeNc68oxgQ=",
        "strip": "proxy-wasm-cpp-host-f2db56af443571e92a31c0b877106d9ea96e19ef",
        "pstrip": 1,
        "patches": [
            (ER, "modules/proxy-wasm-cpp-host/0.0.0-260704-f2db56a.envoy/patches/proxy-wasm-cpp-host.patch",
             bdir + "/proxy-wasm-cpp-host.patch"),
        ],
        "overlays": [
            (ER + "/modules/proxy-wasm-cpp-host/0.0.0-260704-f2db56a.envoy/MODULE.bazel",
             bdir + "/proxy-wasm-cpp-host-overlay-MODULE.patch", "MODULE.bazel", None),
        ],
        "s390x": bdir + "/proxy-wasm-cpp-host-s390x.patch",
    },
    {
        "name": "rules_foreign_cc",
        "url": "https://github.com/bazel-contrib/rules_foreign_cc/releases/download/0.16.0/rules_foreign_cc-0.16.0.tar.gz",
        "integrity": "sha256-Mns/ys3pe5ZlQk2ytsN+b42lnsx4PcW4aDxpOW+CChI=",
        "strip": "rules_foreign_cc-0.16.0",
        "pstrip": 1,
        "patches": [
            (bcr, "modules/rules_foreign_cc/0.16.0/patches/module_dot_bazel_version.patch",
             bdir + "/module_dot_bazel_version.patch"),
        ],
        "overlays": [],
        "s390x": None,
    },
    {
        "name": "rules_rust",
        "url": "https://github.com/bazelbuild/rules_rust/releases/download/0.74.0/rules_rust-0.74.0.tar.gz",
        "integrity": "sha256-2LzB4RHpgnDcAxcuIu96+cL5TzJpgBYYorg9IIkPjfU=",
        "strip": "",
        "pstrip": 1,
        "patches": [
            (ER, "modules/rules_rust/0.74.0.envoy/patches/rules_rust.patch",
             bdir + "/rules_rust-envoy.patch"),
        ],
        "overlays": [],
        "s390x": bdir + "/rules_rust-s390x.patch",
    },
    {
        "name": "grpc",
        "url": "https://github.com/grpc/grpc/archive/v1.83.0.tar.gz",
        "integrity": "sha256-kNRTOTqdQSFd9UYQOxCzO5Vm33nN9vSdxn9sTQRNCQ0=",
        "strip": "grpc-1.83.0",
        "pstrip": 1,
        "patches": [
            (ER, "modules/grpc/1.83.0.envoy/patches/grpc.patch",
             bdir + "/grpc-envoy.patch"),
        ],
        "overlays": [],
        "s390x": bdir + "/grpc-s390x.patch",
    },
    {
        "name": "protobuf",
        "url": "https://github.com/protocolbuffers/protobuf/releases/download/v35.1/protobuf-35.1.tar.gz",
        "integrity": "sha256-8LaDjnUiqNqWEm1IcGjJWbxiSSY2jzAkrI/QOr0KGsQ=",
        "strip": "protobuf-35.1",
        "pstrip": 1,
        "patches": [
            (ER, "modules/protobuf/35.1.bcr.envoy/patches/envoy.patch",
             bdir + "/protobuf-envoy.patch"),
        ],
        "overlays": [],
        "s390x": None,  # protobuf 35.1 does not need s390x patch
    },
    {
        "name": "v8",
        "url": "https://github.com/v8/v8/archive/refs/tags/14.6.202.10.tar.gz",
        "integrity": "sha256-CcPZ95amcfuWMMcZADLwAXHOme/9fIDHquuhSKe8vBs=",
        "strip": "v8-14.6.202.10",
        "pstrip": 1,
        "patches": [
            (ER, "modules/v8/14.6.202.10.envoy/patches/v8.patch",
             bdir + "/v8-envoy.patch"),
            (ER, "modules/v8/14.6.202.10.envoy/patches/requirements.patch",
             bdir + "/v8-requirements.patch"),
        ],
        "overlays": [
            (ER + "/modules/v8/14.6.202.10.envoy/MODULE.bazel",
             bdir + "/v8-overlay-MODULE.patch", "MODULE.bazel",
             "https://raw.githubusercontent.com/v8/v8/refs/tags/14.6.202.10/MODULE.bazel"),
        ],
        "s390x": bdir + "/v8-s390x.patch",
        "extra_patches": [bdir + "/v8-bzlmod.patch", bdir + "/v8-nopip.patch"],
    },
    {
        "name": "toolchains_llvm",
        "url": "https://github.com/bazel-contrib/toolchains_llvm/releases/download/v1.9.1/toolchains_llvm-v1.9.1.tar.gz",
        "integrity": "sha256-QJmeWn22JiaEVxR56Nr7nAre+g775m2yKxN4cheM1HM=",
        "strip": "toolchains_llvm-v1.9.1",
        "pstrip": 1,
        "patches": [
            (ER, "modules/toolchains_llvm/1.9.1.envoy/patches/allow_nonroot.patch",
             bdir + "/allow_nonroot.patch"),
            (ER, "modules/toolchains_llvm/1.9.1.envoy/patches/x_compile.patch",
             bdir + "/x_compile.patch"),
        ],
        "overlays": [],
        "s390x": bdir + "/toolchains_llvm-s390x.patch",
    },
]

os.makedirs(bdir, exist_ok=True)
os.makedirs(bdir + "/external", exist_ok=True)

for dep in deps:
    for (rb, rp, ld) in dep["patches"]:
        fetch(rb + "/" + rp, ld)

def strip_pip_parse(content):
    import re as _re
    content = _re.sub(r'\npip = use_extension\([^\n]*\)\n', '\n', content)
    content = _re.sub(r'\npip\.parse\(.*?\)\n', '\n', content, flags=_re.DOTALL)
    content = _re.sub(r'\nuse_repo\(pip[^)]*\)\n', '\n', content)
    return content

for dep in deps:
    for (url, dest, fname, old_url) in dep["overlays"]:
        new_c = fetch_text(url)
        if dep["name"] == "v8" and fname == "MODULE.bazel":
            new_c = strip_pip_parse(new_c)
        if old_url:
            replace_file_patch(dest, fetch_text(old_url), new_c, fname)
        else:
            add_file_patch(dest, new_c, fname)

def write_v8_nopip_patch(dest):
    import difflib as _dl
    raw = fetch_text("https://raw.githubusercontent.com/v8/v8/refs/tags/14.6.202.10/BUILD.bazel")
    lines = raw.splitlines(keepends=True)
    new_lines = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == 'load("@v8_python_deps//:requirements.bzl", "requirement")':
            i += 1
            continue
        if (lines[i].strip() == 'deps = [' and
                i + 1 < len(lines) and 'requirement("jinja2")' in lines[i+1] and
                i + 2 < len(lines) and lines[i+2].strip() == '],'):
            i += 3
            continue
        new_lines.append(lines[i])
        i += 1
    diff = list(_dl.unified_diff(lines, new_lines,
                                 fromfile="a/BUILD.bazel",
                                 tofile="b/BUILD.bazel"))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w") as f:
        f.writelines(diff)
    print("  wrote v8-nopip patch: " + dest)

write_v8_nopip_patch(bdir + "/v8-nopip.patch")

for dep in deps:
    labels = []
    for (_, dest, _, _) in dep["overlays"]:
        labels.append(blabel(dest))
    for (_, _, ld) in dep["patches"]:
        labels.append(blabel(ld))
    if dep["s390x"]:
        if os.path.exists(dep["s390x"]):
            labels.append(blabel(dep["s390x"]))
        else:
            print("WARNING: missing s390x patch: " + dep["s390x"], file=sys.stderr)
    for ep in dep.get("extra_patches", []):
        if os.path.exists(ep):
            labels.append(blabel(ep))
        else:
            print("WARNING: missing extra patch: " + ep, file=sys.stderr)
    dep["labels"] = labels

def make_override(dep):
    name   = dep["name"]
    labels = dep["labels"]
    out = ("\narchive_override(\n"
           "    module_name = \"" + name + "\",\n"
           "    urls = [\"" + dep["url"] + "\"],\n"
           "    integrity = \"" + dep["integrity"] + "\",\n"
           "    strip_prefix = \"" + dep["strip"] + "\",\n")
    if labels:
        out += "    patches = [\n"
        for lb in labels:
            out += "        \"" + lb + "\",\n"
        out += "    ],\n"
        out += "    patch_strip = " + str(dep["pstrip"]) + ",\n"
    out += ")\n"
    return out

with open("MODULE.bazel") as f:
    mod = f.read()

anchor = "####################################################################################\n# deps: Overrides"
if anchor not in mod:
    for cand in ["single_version_override(", "archive_override("]:
        if cand in mod:
            anchor = cand
            break
    else:
        print("ERROR: anchor not found in MODULE.bazel", file=sys.stderr)
        sys.exit(1)

for dep in deps:
    nm = dep["name"]
    for ot in ["single_version_override", "archive_override"]:
        mod = re.sub(
            r"\n" + ot + r"\(\s*\n\s*module_name\s*=\s*\"" + re.escape(nm) + r"\".*?\n\)\n",
            "", mod, flags=re.DOTALL)
    mod = mod.replace(anchor, make_override(dep) + anchor, 1)
    print("MODULE.bazel: archive_override for " + nm)

_S390X_PLATFORM_TRIPLES = (
    "    supported_platform_triples = [\n"
    "        \"aarch64-apple-darwin\",\n"
    "        \"aarch64-unknown-linux-gnu\",\n"
    "        \"s390x-unknown-linux-gnu\",\n"
    "        \"wasm32-unknown-unknown\",\n"
    "        \"wasm32-wasip1\",\n"
    "        \"x86_64-pc-windows-msvc\",\n"
    "        \"x86_64-unknown-linux-gnu\",\n"
    "        \"x86_64-unknown-nixos-gnu\",\n"
    "    ],\n"
)

def inject_isolated(text):
    result = []
    pos = 0
    marker = "crate.from_cargo("
    while True:
        idx = text.find(marker, pos)
        if idx == -1:
            result.append(text[pos:])
            break
        result.append(text[pos:idx + len(marker)])
        depth = 1
        i = idx + len(marker)
        while i < len(text) and depth > 0:
            if text[i] == "(":   depth += 1
            elif text[i] == ")": depth -= 1
            i += 1
        inner = text[idx + len(marker):i - 1]
        extra = ""
        if "isolated" not in inner:
            extra += "    isolated = False,\n"
        if "supported_platform_triples" not in inner:
            extra += _S390X_PLATFORM_TRIPLES
        result.append(inner + extra)
        result.append(")")
        pos = i
    return "".join(result)

if "isolated = False" not in mod and "crate.from_cargo(" in mod:
    mod = inject_isolated(mod)
    print("MODULE.bazel: injected isolated=False + s390x supported_platform_triples into crate.from_cargo() calls")

def fix_rust_repository_set_target_triple(text):
    import re as _re
    def _add_target_triple(m):
        inner = m.group(1)
        if "target_triple" not in inner:
            exec_m = _re.search(r'exec_triple\s*=\s*"([^"]+)"', inner)
            if exec_m:
                triple_val = exec_m.group(1)
                inner = inner.rstrip() + '\n    target_triple = "' + triple_val + '",\n'
        return "rust.repository_set(" + inner + ")"
    return _re.sub(r"rust\.repository_set\(([^)]+)\)", _add_target_triple, text, flags=_re.DOTALL)

if "rust.repository_set(" in mod:
    mod = fix_rust_repository_set_target_triple(mod)
    print("MODULE.bazel: fixed rust.repository_set() target_triple")

# ---- luajit archive_override ----
for ot in ["single_version_override", "archive_override"]:
    mod = re.sub(
        r"\n" + ot + r"\(\s*\n\s*module_name\s*=\s*\"luajit\".*?\n\)\n",
        "", mod, flags=re.DOTALL)

luajit_stanza = (
    "\narchive_override(\n"
    "    module_name = \"luajit\",\n"
    "    urls = [\"https://github.com/LuaJIT/LuaJIT/archive/871db2c.tar.gz\"],\n"
    "    integrity = \"sha256-qz8W2C32lGVDVlz7DSgQ04fXmjpD4EMWlbA0ZhiOJoA=\",\n"
    "    strip_prefix = \"LuaJIT-871db2c84ecefd70a850e03a6c340214a81739f0\",\n"
    "    patches = [\n"
    "        \"//bazel:luajit-overlay-BUILD.patch\",\n"
    "        \"//bazel:luajit-overlay-MODULE.patch\",\n"
    "        \"//bazel:luajit-s390x-dynasm.patch\",\n"
    "        \"//bazel:luajit-s390x-vm.patch\",\n"
    "        \"//bazel:luajit-s390x-fix.patch\",\n"
    "        \"//bazel:luajit-as.patch\",\n"
    "    ],\n"
    "    patch_strip = 1,\n"
    ")\n"
)
mod = mod.replace(anchor, luajit_stanza + anchor, 1)
print("MODULE.bazel: archive_override for luajit")

# ---- highway single_version_override with s390x patch ----
highway_s390x_patch = bdir + "/highway-s390x.patch"
if os.path.exists(highway_s390x_patch):
    for ot in ["single_version_override", "archive_override"]:
        mod = re.sub(
            r"\n" + ot + r"\(\s*\n\s*module_name\s*=\s*\"highway\".*?\n\)\n",
            "", mod, flags=re.DOTALL)
    highway_stanza = (
        "\nsingle_version_override(\n"
        "    module_name = \"highway\",\n"
        "    version = \"1.2.0\",\n"
        "    patches = [\"//bazel:highway-s390x.patch\"],\n"
        "    patch_strip = 1,\n"
        ")\n"
    )
    mod = mod.replace(anchor, highway_stanza + anchor, 1)
    print("MODULE.bazel: single_version_override for highway (s390x patch)")

def inject_luajit_s390x(content):
    setting_s390x = """
config_setting(
    name = "linux_s390x",
    constraint_values = [
        "@platforms//cpu:s390x",
        "@platforms//os:linux",
    ],
)
"""
    insert_before1 = "\n##############################################################################\n# Stage 1"
    if insert_before1 in content and "linux_s390x" not in content:
        content = content.replace(insert_before1, setting_s390x + insert_before1, 1)

    arch_h_s390x = """
genrule(
    name = "buildvm_arch_h_s390x",
    srcs = ["src/vm_s390x.dasc"] + _DYNASM_SRCS,
    outs = ["_s390x/buildvm_arch.h"],
    cmd = "$(location :minilua) $(location dynasm/dynasm.lua) -D S390X -D ENDIAN_BE -D P64 -D FFI -D DUALNUM -D FPU -D HFABI -D VER=0 -o $@ $(location src/vm_s390x.dasc)",
    tools = [":minilua"],
)
"""
    insert_before3 = "\n##############################################################################\n# Stage 3"
    if insert_before3 in content and "buildvm_arch_h_s390x" not in content:
        content = content.replace(insert_before3, arch_h_s390x + insert_before3, 1)

    buildvm_s390x_rule = """
cc_binary(
    name = "buildvm_s390x",
    srcs = _BUILDVM_SRCS + [":buildvm_arch_h_s390x"],
    copts = _BUILDVM_COPTS,
    includes = _BUILDVM_INCLUDES + ["_s390x"],
    local_defines = _BUILDVM_DEFINES + ["LUAJIT_TARGET=LUAJIT_ARCH_S390X"],
)
"""
    insert_before4 = "\n##############################################################################\n# Stage 4"
    if insert_before4 in content and "buildvm_s390x" not in content:
        content = content.replace(insert_before4, buildvm_s390x_rule + insert_before4, 1)

    import re as _re
    _sed = "sed " + chr(39) + "s/.hword/.2byte/g" + chr(39)
    _s390x_elfasm = (
        "        " + chr(34) + ":linux_s390x" + chr(34) + ": "
        + chr(34) + "$(location :buildvm_s390x) -m elfasm -o $@.tmp && " + _sed + " $@.tmp > $@" + chr(34) + ",\n"
    )
    content = _re.sub(
        r'(":linux_arm64": "\$\(location :buildvm_arm64\) -m elfasm -o \$@",\n)',
        r'\1' + _s390x_elfasm,
        content)
    content = _re.sub(
        r'(":linux_arm64": \[":buildvm_arm64"\],)',
        r'\1\n        ":linux_s390x": [":buildvm_s390x"],',
        content)
    for mode in ["bcdef", "ffdef", "libdef", "recdef", "folddef"]:
        content = _re.sub(
            r'(":linux_arm64": "\$\(location :buildvm_arm64\) -m ' + mode + r' -o \$@ \$\(SRCS\)",)',
            r'\1\n        ":linux_s390x": "$(location :buildvm_s390x) -m ' + mode + r' -o $@ $(SRCS)",',
            content)
    return content

reg_base = envoy_reg + "/modules/luajit/0.0.0-260126-871db2c.envoy/overlay"
for (fname, dest) in [("BUILD.bazel",  bdir + "/luajit-overlay-BUILD.patch"),
                       ("MODULE.bazel", bdir + "/luajit-overlay-MODULE.patch")]:
    raw = fetch_text(reg_base + "/" + fname)
    if fname == "BUILD.bazel":
        raw = inject_luajit_s390x(raw)
    add_file_patch(dest, raw, fname)

fetch_patch("https://github.com/iii-i/moonjit/commit/dee73f516f0da49e930dcfa1dd61720dcb69b7dd.patch",
            bdir + "/luajit-s390x-dynasm.patch")
fetch_patch("https://github.com/iii-i/moonjit/commit/035f133798adb856391928600f7cb6b4f81578ab.patch",
            bdir + "/luajit-s390x-vm.patch")
fetch_patch("https://github.com/openresty/luajit2/commit/e598aeb7426dbc069f90ba70db9bce43cd573b0e.patch",
            bdir + "/luajit-s390x-fix.patch")

# ---- llvm_s390x toolchain extension ----
for pat in [
    r"\nllvm_s390x\s*=\s*use_extension\([^\n]*\)\n"
    r"llvm_s390x\.toolchain\([^)]*\)\n"
    r"llvm_s390x\.toolchain_root\([^)]*\)\n"
    r"use_repo\(llvm_s390x[^)]*\)\n",
    r'register_toolchains\("@llvm_toolchain_s390x//:all"\)\n',
]:
    mod = re.sub(pat, "", mod, flags=re.DOTALL)

s390x_ext = (
    "\nllvm_s390x = use_extension("
    "\"@toolchains_llvm//toolchain/extensions:llvm.bzl\", \"llvm\")\n"
    "llvm_s390x.toolchain(\n"
    "    name = \"llvm_toolchain_s390x\",\n"
    "    llvm_version = \"22.1.8\",\n"
    ")\n"
    "llvm_s390x.toolchain_root(\n"
    "    name = \"llvm_toolchain_s390x\",\n"
    "    path = \"" + llvm_path + "\",\n"
    "    targets = [\"linux-s390x\"],\n"
    ")\n"
    "use_repo(llvm_s390x, \"llvm_toolchain_s390x\")\n"
)

marker = "use_repo(llvm, \"llvm_toolchain\")"
if marker in mod:
    mod = mod.replace(marker, s390x_ext + marker, 1)
    print("MODULE.bazel: inserted llvm_toolchain_s390x")
else:
    print("WARNING: llvm marker not found, appending s390x ext")
    mod += s390x_ext

reg_tc = "register_toolchains(\"@llvm_toolchain//:all\")"
if reg_tc in mod and "register_toolchains(\"@llvm_toolchain_s390x//:all\")" not in mod:
    mod = mod.replace(reg_tc,
                      reg_tc + "\nregister_toolchains(\"@llvm_toolchain_s390x//:all\")", 1)
    print("MODULE.bazel: registered llvm_toolchain_s390x")

# ---- envoy_llvm_extension: inject host() tag so s390x bypasses the
#      _llvm_alias_repo platform check (only supports x86/arm/mac).
#      Without this, the build fails with "Unsupported host platform for
#      llvm_toolchain_llvm: linux s390x" on any s390x host. ----
_ENVOY_LLVM_EXT_MARKER = 'envoy_llvm_ext = use_extension("//bazel:extensions.bzl", "envoy_llvm_extension")'
_ENVOY_LLVM_HOST_TAG = (
    'envoy_llvm_ext = use_extension("//bazel:extensions.bzl", "envoy_llvm_extension")\n'
    'envoy_llvm_ext.host(\n'
    '    llvm_version = "22.1.8",\n'
    '    path = "' + llvm_path + '",\n'
    ')\n'
)
if _ENVOY_LLVM_EXT_MARKER in mod:
    if 'envoy_llvm_ext.host(' not in mod:
        mod = mod.replace(_ENVOY_LLVM_EXT_MARKER, _ENVOY_LLVM_HOST_TAG, 1)
        print("MODULE.bazel: injected envoy_llvm_ext.host() for s390x")
    else:
        print("MODULE.bazel: envoy_llvm_ext.host() already present")
else:
    print("WARNING: envoy_llvm_ext marker not found — skipping host() injection")

# ---- go_sdk.download(): add linux_s390x entry ----
# The go_sdk.download() block in MODULE.bazel only lists amd64/arm64/darwin
# platforms.  On s390x rules_go fails with "unsupported platform linux_s390x"
# during the gazelle extension load.  We inject the s390x SDK entry so
# rules_go can resolve the toolchain without trying to download anything
# (the system Go in /usr/local/go is used at build time via GOPATH, but
# rules_go still needs to parse the sdks dict during analysis).
# Inject linux_s390x into go_sdk.download() sdks dict.
# The upstream block only lists amd64/arm64/darwin — rules_go fails with
# "unsupported platform linux_s390x" during analysis without this entry.
_GO_S390X_LINE = (
    '        "linux_s390x": ("go1.27.1.linux-s390x.tar.gz",'
    ' "c9e1ad7bddea40e2eb10b4d3267ae06d462667839bf0ba94c2e2d160b06f9b9e"),\n'
)
_GO_ANCHOR = '        "darwin_amd64":'
# Check specifically inside the go_sdk.download() block, not the whole file
# (linux_s390x already appears in rust.repository_set stanzas above).
_go_sdk_idx = mod.find('go_sdk.download(')
_go_sdk_end = mod.find(')', _go_sdk_idx) if _go_sdk_idx != -1 else -1
_go_sdk_block = mod[_go_sdk_idx:_go_sdk_end] if _go_sdk_idx != -1 else ''
if 'go_sdk.download(' in mod and 'linux_s390x' not in _go_sdk_block:
    # Find the darwin_amd64 line (always last in the upstream sdks dict)
    # and insert s390x right after it.
    idx = mod.find(_GO_ANCHOR)
    if idx != -1:
        eol = mod.index("\n", idx)
        mod = mod[:eol+1] + _GO_S390X_LINE + mod[eol+1:]
        print("MODULE.bazel: injected linux_s390x into go_sdk.download()")
    else:
        print("WARNING: go_sdk.download darwin_amd64 anchor not found")

with open("MODULE.bazel", "w") as f:
    f.write(mod)
print("MODULE.bazel: done")
