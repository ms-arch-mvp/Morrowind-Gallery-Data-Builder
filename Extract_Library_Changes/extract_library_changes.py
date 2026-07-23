import sys
import os
import hashlib
import shutil
import time
import configparser
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HASH_BUF = 1 << 20  # 1 MiB

_TRUE = {"1", "true", "yes", "on"}


def load_settings():
    """Read config.ini next to this script; return the [settings] section as a dict."""
    cfg = configparser.ConfigParser()
    path = os.path.join(SCRIPT_DIR, "config.ini")
    if not cfg.read(path, encoding="utf-8") or not cfg.has_section("settings"):
        print(f"ERROR: missing or malformed config.ini next to the script:\n  {path}")
        sys.exit(2)
    return cfg["settings"]


def walk_files(root, subfolders):
    """rel_lower -> (rel_original, full_path, size)."""
    out = {}
    for sub in subfolders:
        base = os.path.join(root, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, filenames in os.walk(base):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                out[rel.lower()] = (rel, full, size)
    return out


def file_hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(HASH_BUF), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if len(sys.argv) < 3:
        print("usage: extract_library_changes.py <new_lib> <old_lib>")
        sys.exit(2)
    new_lib, old_lib = sys.argv[1], sys.argv[2]

    s = load_settings()
    subfolders = [x.strip() for x in s.get("subfolders", "meshes, textures").split(",") if x.strip()]
    quick = s.get("quick", "false").strip().lower() in _TRUE
    write_flagged = s.get("flagged_meshes", "true").strip().lower() in _TRUE
    try:
        workers = int(s.get("workers", "-1"))
    except ValueError:
        workers = -1
    if workers <= 0:
        workers = os.cpu_count() or 1
    output_dir = os.path.join(SCRIPT_DIR, "output")

    for label, path in [("New library", new_lib), ("Old library", old_lib)]:
        if not os.path.isdir(path):
            print(f"ERROR: {label} folder does not exist:\n  {path}")
            sys.exit(1)

    print(f"Scanning new library: {new_lib}")
    new_files = walk_files(new_lib, subfolders)
    print(f"Scanning old library: {old_lib}")
    old_files = walk_files(old_lib, subfolders)
    print(f"  new: {len(new_files)} file(s)   old: {len(old_files)} file(s)")
    print(f"  comparing subfolders: {', '.join(subfolders)}"
          f"   mode: {'quick (size only)' if quick else 'size + hash'}\n")

    def classify(item):
        _key, (rel, full, size) = item
        old = old_files.get(_key)
        if old is None:
            return ("NEW", rel, full, None)
        _orel, ofull, osize = old
        if size != osize:
            return ("MODIFIED", rel, full, f"size {osize}->{size}")
        if quick:
            return ("SAME", rel, full, None)
        if file_hash(full) != file_hash(ofull):
            return ("MODIFIED", rel, full, "content changed (same size)")
        return ("SAME", rel, full, None)

    items = sorted(new_files.items())
    new_only, modified, same = [], [], 0
    started = time.time()

    # Threads, not processes: the work is I/O-bound (hashing reads files), which
    # threads speed up, and they don't spawn child processes -- so the launcher's
    # `pause` is never disturbed. Results come back in submission order.
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (cat, rel, full, why) in enumerate(ex.map(classify, items), 1):
            if cat == "NEW":
                new_only.append((rel, full))
            elif cat == "MODIFIED":
                modified.append((rel, full, why))
            else:
                same += 1
            if i % 500 == 0 or i == len(items):
                print(f"\r  compared {i}/{len(items)}  ({time.time() - started:.0f}s)   ",
                      end="", flush=True)
    print()

    to_copy = [(rel, full, "NEW") for rel, full in new_only] + \
              [(rel, full, "MODIFIED") for rel, full, _why in modified]

    for rel, full, _tag in to_copy:
        dest = os.path.join(output_dir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(full, dest)

    os.makedirs(output_dir, exist_ok=True)

    # flagged_meshes.txt for the MWSE Thumbnail Generator: one record-style mesh
    # path per line (relative to meshes\, backslashes, leading "meshes\" dropped).
    # Only .nif files -- textures/.kf are not render subjects.
    flagged = []
    if write_flagged:
        for rel, _full, _tag in to_copy:
            low = rel.lower()
            if low.startswith("meshes/") and low.endswith(".nif"):
                flagged.append(rel[len("meshes/"):].replace("/", "\\"))
        with open(os.path.join(output_dir, "flagged_meshes.txt"), "w", encoding="utf-8") as f:
            for line in sorted(flagged):
                f.write(line + "\n")

    with open(os.path.join(output_dir, "_report.txt"), "w", encoding="utf-8") as f:
        f.write("Library comparison report\n")
        f.write(f"  generated : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"  new library: {new_lib}\n")
        f.write(f"  old library: {old_lib}\n")
        f.write(f"  subfolders : {', '.join(subfolders)}\n")
        f.write(f"  mode       : {'quick (size only)' if quick else 'size + hash'}\n\n")
        f.write(f"NEW files ({len(new_only)}):\n")
        for rel, _full in new_only:
            f.write(f"  {rel}\n")
        f.write(f"\nMODIFIED files ({len(modified)}):\n")
        for rel, _full, why in modified:
            f.write(f"  {rel}   [{why}]\n")
        # files removed in the new library (present in old, gone in new)
        removed = [old_files[k][0] for k in old_files if k not in new_files]
        f.write(f"\nREMOVED in new library ({len(removed)}) - not copied, listed only:\n")
        for rel in sorted(removed):
            f.write(f"  {rel}\n")

    print("\n" + "=" * 48)
    print(f"  NEW      : {len(new_only)}")
    print(f"  MODIFIED : {len(modified)}")
    print(f"  unchanged: {same}")
    print(f"  copied   : {len(to_copy)} file(s) -> {output_dir}")
    if write_flagged:
        print(f"  flagged  : {len(flagged)} .nif path(s) -> flagged_meshes.txt")
    print("=" * 48)


if __name__ == "__main__":
    main()
