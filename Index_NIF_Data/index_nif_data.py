import sys
import os
import io
import glob
import json
import time
import shutil
import warnings
import contextlib
import configparser
from concurrent.futures import ProcessPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLOAT_DIGITS = 6
_TRUE = {"1", "true", "yes", "on"}


def load_settings():
    cfg = configparser.ConfigParser()
    path = os.path.join(SCRIPT_DIR, "config.ini")
    if not cfg.read(path, encoding="utf-8") or not cfg.has_section("settings"):
        print(f"ERROR: missing or malformed config.ini next to the script:\n  {path}")
        sys.exit(2)
    return cfg["settings"]

# Arrays with more than this many elements collapse to a plain count instead of
# their contents. Keeps small structural arrays (lod_levels, 4x4 transforms)
# inline while bulk geometry never gets written out.
INLINE_LIMIT = 64


def find_es3_lib():
    """es3 lives in the io_scene_mw addon under the Blender user config. Glob for
    it (works without the Blender app, so we can run on Blender's python.exe)."""
    pattern = os.path.join(os.path.expandvars(r"%APPDATA%"), "Blender Foundation",
                           "Blender", "*", "scripts", "addons", "io_scene_mw", "lib")
    hits = sorted(glob.glob(pattern))
    return hits[-1] if hits else None


ES3_LIB = find_es3_lib()
if not ES3_LIB or not os.path.isdir(ES3_LIB):
    print("ERROR: could not locate the es3 library.\n"
          "  No io_scene_mw addon found under %APPDATA%\\Blender Foundation --\n"
          "  make sure the Morrowind NIF importer addon is installed.")
    sys.exit(2)

sys.path.insert(0, ES3_LIB)
try:
    from es3 import nif
except ImportError as e:
    print(f"ERROR: could not import es3 from:\n  {ES3_LIB}\n  {e}")
    sys.exit(2)


def encode_value(value, index_of):
    """Convert one NIF field to something JSON can hold."""
    if value is None or isinstance(value, (bool, int, str)):
        return value

    if isinstance(value, float):
        return round(value, FLOAT_DIGITS)

    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")

    if isinstance(value, nif.NiObject):
        return {"$ref": index_of.get(id(value), -1)}

    # numpy array (vertices, lod_levels, matrices, ...)
    if hasattr(value, "__array__"):
        try:
            size = int(value.size)
            shape = [int(d) for d in value.shape]
        except Exception:
            return str(value)
        if size == 0:
            return []
        # bulk geometry -> just the element/row count, never the data
        if size > INLINE_LIMIT:
            return shape[0]
        try:
            # Round in float64: rounding a float32 near its max (e.g. a LOD
            # far-extent of FLT_MAX = 3.4e38) overflows to inf, which is both a
            # RuntimeWarning and invalid JSON. float64 has the headroom.
            if value.dtype.kind == "f":
                return value.astype("float64").round(FLOAT_DIGITS).tolist()
            return value.tolist()
        except (TypeError, AttributeError):
            return value.tolist()

    # list / tuple -- may hold child objects or plain values
    if isinstance(value, (list, tuple)):
        if len(value) > INLINE_LIMIT:
            return len(value)
        return [encode_value(v, index_of) for v in value]

    # anything else (nested helper structs such as TexturingPropertyMap)
    if hasattr(value, "attributes"):
        return encode_block(value, index_of, with_index=False)

    return str(value)


# Bulk per-vertex geometry: dropped entirely. Triangle data is kept but, like any
# large array, comes through encode_value as a plain count.
DROP_FIELDS = {"vertices", "normals", "vertex_colors", "uv_sets", "shared_normals"}


def encode_block(obj, index_of, with_index=True):
    out = {}
    if with_index:
        out["index"] = index_of.get(id(obj), -1)
    out["type"] = obj.type
    for name in sorted(obj.attributes()):
        if name in DROP_FIELDS:
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        out[name] = encode_value(value, index_of)
    return out


def dump_nif(path):
    stream = nif.NiStream()
    stream.load(path)
    blocks = list(stream.objects())
    index_of = {id(o): i for i, o in enumerate(blocks)}
    return {
        "num_blocks": len(blocks),
        "roots": [index_of.get(id(r), -1) for r in stream.roots],
        "blocks": [encode_block(o, index_of) for o in blocks],
    }


def parse_worker(full):
    """Pool worker: parse one NIF, returning (True, doc) or (False, error).
    Silences es3's own prints and numpy warnings so the console stays clean."""
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()), \
             warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return (True, dump_nif(full))
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


def file_contains_any(path, needles):
    """Cheap prefilter: block type names are plain ASCII in the file, so a raw
    byte search skips files that cannot match before the expensive parse."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return False
    return any(n in raw for n in needles)


# ---- input collection ---------------------------------------------------
# The tool switches mode by what you paste:
#   * a Mod Organizer 2 instance (folder with ModOrganizer.ini) -> resolve the
#     active load order and index the winning copy of every loose mesh
#   * any other folder -> index every .nif found beneath it
# Each job is (key, full_path, source_mod|None); key is the mesh's identifier.

def read_selected_profile(base):
    """Active MO2 profile from ModOrganizer.ini (value looks like @ByteArray(Default))."""
    profile = "Default"
    try:
        with open(os.path.join(base, "ModOrganizer.ini"), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("selected_profile"):
                    val = line.split("=", 1)[1].strip()
                    if val.startswith("@ByteArray(") and val.endswith(")"):
                        val = val[len("@ByteArray("):-1]
                    if val:
                        profile = val
                    break
    except OSError:
        pass
    return profile


def collect_mo2_active(base):
    """Resolve the active load order to the winning loose .nif per game path.

    modlist.txt lists mods top = highest priority; overwrite outranks all. The
    first mod in that order to supply a given meshes-relative path wins (matched
    case-insensitively, like the game's virtual file system)."""
    profile = read_selected_profile(base)
    modlist = os.path.join(base, "profiles", profile, "modlist.txt")
    if not os.path.isfile(modlist):
        print(f"ERROR: modlist not found for profile '{profile}':\n  {modlist}")
        sys.exit(1)

    enabled = []
    with open(modlist, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("+") and not line.endswith("_separator"):
                enabled.append(line[1:])

    search = [(os.path.join(base, "overwrite"), "overwrite")]
    search += [(os.path.join(base, "mods", m), m) for m in enabled]

    winners = {}  # game_rel_lower -> (game_rel, full, mod)
    for entry, mod in search:
        meshes_dir = os.path.join(entry, "meshes")
        if not os.path.isdir(meshes_dir):
            continue
        for dirpath, _dirs, filenames in os.walk(meshes_dir):
            for fn in filenames:
                if fn.lower().endswith(".nif"):
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, meshes_dir).replace("\\", "/")
                    key = rel.lower()
                    if key not in winners:
                        winners[key] = (rel, full, mod)

    jobs = sorted(winners.values(), key=lambda j: j[0].lower())
    return profile, len(enabled), jobs


def collect_folder(root):
    jobs = []
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".nif"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace("\\", "/")
                jobs.append((rel, full, None))
    return sorted(jobs, key=lambda j: j[0].lower())


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if len(argv) < 1:
        print("usage: index_nif_data.py -- <input_folder>")
        sys.exit(2)

    root = argv[0]
    if not os.path.isdir(root):
        print(f"ERROR: input folder does not exist:\n  {root}")
        sys.exit(1)

    settings = load_settings()
    filter_names = [x.strip() for x in settings.get("node_filter", "").split(",") if x.strip()]
    filter_set = {n.lower() for n in filter_names}
    needles = [n.encode("ascii", "ignore") for n in filter_names]
    extract = settings.get("extract", "false").strip().lower() in _TRUE
    write_flagged = settings.get("flagged_meshes", "false").strip().lower() in _TRUE
    try:
        workers = int(settings.get("workers", "-1"))
    except ValueError:
        workers = -1
    if workers <= 0:
        workers = os.cpu_count() or 1

    output_dir = os.path.join(SCRIPT_DIR, "output")
    extract_dir = os.path.join(output_dir, "extracted")

    if os.path.isfile(os.path.join(root, "ModOrganizer.ini")):
        profile, mod_count, jobs = collect_mo2_active(root)
        mode = f"mo2 load order (profile '{profile}', {mod_count} enabled mods)"
        out_name = os.path.basename(os.path.normpath(root)) + "_" + profile
        print(f"MO2 instance detected. Resolving active load order "
              f"(profile '{profile}', {mod_count} enabled mods)...")
    else:
        jobs = collect_folder(root)
        mode = "folder"
        out_name = os.path.basename(os.path.normpath(root))

    if filter_names:
        out_name += "_" + "-".join(n.replace(" ", "") for n in filter_names)

    out_path = os.path.join(output_dir, out_name + ".json")
    print(f"Scanning {len(jobs)} .nif file(s)"
          + (f"; keeping only those with: {', '.join(filter_names)}" if filter_names else "")
          + (" (+extracting)" if extract else ""))
    if not jobs:
        print("Nothing to do.")
        return

    # cheap byte-level prefilter so only candidate files reach the parser
    if filter_names:
        candidates = [(k, f, m) for (k, f, m) in jobs if file_contains_any(f, needles)]
        print(f"  {len(candidates)} candidate(s) contain the filter term(s)")
    else:
        candidates = jobs

    meshes, failed, extracted = {}, [], 0
    started = time.time()
    paths = [c[1] for c in candidates]

    # Parse in parallel; each worker re-imports es3. Small jobs stay sequential to
    # avoid pool startup overhead. Results come back in submission order.
    ex = None
    if workers > 1 and len(paths) > 50:
        ex = ProcessPoolExecutor(max_workers=workers)
        results = ex.map(parse_worker, paths, chunksize=64)
        print(f"  parsing with {workers} worker(s)...")
    else:
        results = (parse_worker(p) for p in paths)

    for i, ((key, full, mod), (ok, data)) in enumerate(zip(candidates, results), 1):
        if i % 500 == 0 or i == len(candidates):
            print(f"\r  {i}/{len(candidates)}  ({time.time() - started:.0f}s, {len(meshes)} kept)   ",
                  end="", flush=True)
        if not ok:
            failed.append({"file": key, "error": data})
            continue
        # confirm the block really is present (prefilter can false-positive)
        if filter_names:
            types = {b["type"].lower() for b in data["blocks"]}
            if not (types & filter_set):
                continue
        if mod is not None:
            data["source_mod"] = mod
        meshes[key] = data
        if extract:
            dest = os.path.join(extract_dir, key.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(full, dest)
            extracted += 1

    if ex is not None:
        ex.shutdown()
    print()   # end the running progress line

    doc = {
        "source_folder": os.path.abspath(root),
        "mode": mode,
        "node_filter": filter_names,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nif_count": len(meshes),
        "failed_count": len(failed),
        "nif_version": "4.0.0.2",
        "note": ("Per-vertex geometry (vertices, normals, uvs, vertex colours) is "
                 "dropped; the triangle count is kept as a plain number. Object "
                 "references are {'$ref': block_index}. Block indices follow "
                 "root-traversal order; this matched NifSkope's on-disk numbering "
                 "on the files spot-checked, but is not guaranteed to. es3 only "
                 "reads NIF 4.0.0.2 (Morrowind) and raises on anything else -- "
                 "such files appear in 'failed'."),
        "failed": failed,
        "meshes": meshes,
    }

    os.makedirs(output_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)

    # flagged_meshes.txt for the MWSE Thumbnail Generator: kept meshes as
    # record-style paths (relative to meshes\, backslashes, leading meshes\ dropped).
    flagged_count = 0
    if write_flagged:
        flagged = []
        for key in meshes:
            k = key[len("meshes/"):] if key.lower().startswith("meshes/") else key
            flagged.append(k.replace("/", "\\"))
        with open(os.path.join(output_dir, "flagged_meshes.txt"), "w", encoding="utf-8") as f:
            for line in sorted(flagged):
                f.write(line + "\n")
        flagged_count = len(flagged)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"\nWrote {out_path}  ({size_mb:.1f} MB)")
    print(f"  kept   : {len(meshes)}")
    print(f"  failed : {len(failed)}")
    if extract:
        print(f"  extracted {extracted} .nif file(s) -> {extract_dir}")
    if write_flagged:
        print(f"  flagged  {flagged_count} .nif path(s) -> flagged_meshes.txt")
    for e in failed[:10]:
        print(f"     {e['file']}: {e['error']}")


if __name__ == "__main__":
    main()
