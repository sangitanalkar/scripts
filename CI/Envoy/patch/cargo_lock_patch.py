import json, copy, sys, os

os.chdir(os.environ.get("ENVOY_DIR", "."))
lock_path = "Cargo.Bazel.lock"
with open(lock_path) as fh:
    data = json.load(fh)

T_S390  = "s390x-unknown-linux-gnu"
T_ARM64 = "aarch64-unknown-linux-gnu"
modified = False

def mirror(v):
    changed = False
    if isinstance(v, dict):
        if T_ARM64 in v and T_S390 not in v:
            v[T_S390] = copy.deepcopy(v[T_ARM64])
            changed = True
        for child in v.values():
            changed |= mirror(child)
    elif isinstance(v, list):
        for item in v:
            changed |= mirror(item)
    return changed

modified |= mirror(data.get("crates", {}))

conds = data.get("conditions", {})
if T_S390 not in conds:
    conds[T_S390] = [T_S390]
    modified = True
for val in conds.values():
    if isinstance(val, list) and T_ARM64 in val and T_S390 not in val:
        val.insert(val.index(T_ARM64) + 1, T_S390)
        modified = True

if data.get("checksum"):
    data["checksum"] = ""
    modified = True

if modified:
    with open(lock_path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print("Cargo.Bazel.lock: injected s390x entries")
else:
    print("Cargo.Bazel.lock: s390x already present")
