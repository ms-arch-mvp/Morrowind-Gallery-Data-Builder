import os
import re
import sys
import json
import time
import tempfile
import subprocess
import configparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TES3CONV = os.path.join(os.path.dirname(SCRIPT_DIR), "Shared", "tes3conv.exe")
_TRUE = {"1", "true", "yes", "on"}
# Keep each tes3conv child from flashing its own console window.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Lua's script.id:gsub("[^%w_%-%s]", "_") -- keep ASCII alnum, underscore, hyphen,
# whitespace; everything else becomes "_". %w is ASCII-only, so pin the class to ASCII.
_UNSAFE = re.compile(r"[^A-Za-z0-9_\-\s]")


def load_settings():
    cfg = configparser.ConfigParser()
    path = os.path.join(SCRIPT_DIR, "config.ini")
    if not cfg.read(path, encoding="utf-8") or not cfg.has_section("settings"):
        print(f"ERROR: missing or malformed config.ini next to the script:\n  {path}")
        sys.exit(2)
    return cfg["settings"]


# ---- MO2 resolution -----------------------------------------------------

def _ini_value(raw):
    """Strip an MO2 @ByteArray(...) wrapper and unescape doubled backslashes."""
    val = raw.strip()
    if val.startswith("@ByteArray(") and val.endswith(")"):
        val = val[len("@ByteArray("):-1]
    return val.replace("\\\\", "\\")


def read_mo2_ini(base):
    """(selected_profile, game_path) from ModOrganizer.ini."""
    profile, game_path = "Default", None
    try:
        with open(os.path.join(base, "ModOrganizer.ini"), encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                low = s.lower()
                if low.startswith("selected_profile"):
                    profile = _ini_value(s.split("=", 1)[1]) or profile
                elif low.startswith("gamepath"):
                    game_path = _ini_value(s.split("=", 1)[1])
    except OSError:
        pass
    return profile, game_path


_GAMEFILE = re.compile(r"GameFile(\d+)\s*=\s*(.+)", re.IGNORECASE)


def read_active_plugins(ini_path):
    """Active plugins, in load order, from a Morrowind.ini [Game Files] section.
    This is what Morrowind.exe actually loads -- the authoritative enabled set."""
    entries = []
    try:
        with open(ini_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _GAMEFILE.match(line.strip())
                if m:
                    name = m.group(2).strip()
                    if name:
                        entries.append((int(m.group(1)), name))
    except OSError:
        return []
    entries.sort()
    return [name for _idx, name in entries]


def read_local_settings(prof_dir):
    """MO2 'LocalSettings' flag: true -> game reads the profile's Morrowind.ini,
    false -> it reads the real game Morrowind.ini."""
    try:
        with open(os.path.join(prof_dir, "settings.ini"), encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip().lower().replace(" ", "")
                if s.startswith("localsettings="):
                    return s.split("=", 1)[1] in _TRUE
    except OSError:
        pass
    return False


def read_lines(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
    except OSError:
        pass
    return out


def collect_mo2_active(base, plugins_mode):
    """Resolve the plugin list to (plugin_name, physical_path) pairs, in load
    order. Plugin files are matched by name; the winning copy is the first hit
    walking overwrite > enabled mods (modlist priority) > game Data Files.

    plugins_mode:
      'enabled' -> only the active load order (Morrowind.ini [Game Files]); this
                   is what the game actually loads, so disabled plugins that merely
                   sit in Data Files (e.g. unused official add-ons) are excluded.
      'all'     -> every plugin MO2 lists (loadorder.txt), enabled or not."""
    profile, game_path = read_mo2_ini(base)
    game_data = os.path.join(game_path, "Data Files") if game_path else None
    prof_dir = os.path.join(base, "profiles", profile)

    enabled = []
    for line in read_lines(os.path.join(prof_dir, "modlist.txt")):
        if line.startswith("+") and not line.endswith("_separator"):
            enabled.append(line[1:])

    # priority order for resolving the physical plugin file
    search = [os.path.join(base, "overwrite")]
    search += [os.path.join(base, "mods", m) for m in enabled]
    if game_data:
        search.append(game_data)

    plugin_files = {}  # name_lower -> full path (first hit wins)
    for entry in search:
        try:
            names = os.listdir(entry)
        except OSError:
            continue
        for fn in names:
            low = fn.lower()
            if low.endswith(".esp") or low.endswith(".esm"):
                plugin_files.setdefault(low, os.path.join(entry, fn))

    if plugins_mode == "all":
        wanted = read_lines(os.path.join(prof_dir, "loadorder.txt"))
        source = "all managed plugins (loadorder.txt)"
        if not wanted:
            print(f"ERROR: no loadorder.txt for profile '{profile}':\n  {prof_dir}")
            sys.exit(1)
    else:
        # authoritative active set: Morrowind.ini [Game Files]. LocalSettings picks
        # which ini the game reads; fall back to the other if the chosen one is bare.
        local = read_local_settings(prof_dir)
        prof_ini = os.path.join(prof_dir, "Morrowind.ini")
        game_ini = os.path.join(game_path, "Morrowind.ini") if game_path else None
        primary, secondary = (prof_ini, game_ini) if local else (game_ini, prof_ini)
        wanted = read_active_plugins(primary) if primary else []
        if not wanted and secondary:
            wanted = read_active_plugins(secondary)
        source = "active plugins (Morrowind.ini [Game Files])"
        if not wanted:
            print("ERROR: could not read the active plugin list from Morrowind.ini "
                  "[Game Files].\n  Looked in the game and profile Morrowind.ini. "
                  "Use plugins = all to fall back to loadorder.txt.")
            sys.exit(1)

    jobs, missing = [], []
    for name in wanted:
        path = plugin_files.get(name.lower())
        if path:
            jobs.append((name, path))
        else:
            missing.append(name)
    return profile, len(enabled), jobs, missing, source


def collect_folder(root):
    """Every .esp/.esm beneath root, ordered by name. Each stands on its own."""
    jobs = []
    for dirpath, _dirs, filenames in os.walk(root):
        for fn in filenames:
            low = fn.lower()
            if low.endswith(".esp") or low.endswith(".esm"):
                jobs.append((fn, os.path.join(dirpath, fn)))
    return sorted(jobs, key=lambda j: j[0].lower())


# ---- extraction ---------------------------------------------------------

def format_global(value):
    """Match MWSE's string.format('%s', global.value): ints print plain, floats
    drop trailing zeros (Lua uses %.14g)."""
    vtype = (value or {}).get("type", "")
    data = (value or {}).get("data", 0)
    try:
        if vtype in ("Short", "Long"):
            return str(int(data))
        return "%.14g" % float(data)
    except (TypeError, ValueError):
        return str(data)


def extract_plugin(job):
    """Run tes3conv on one plugin and pull out its scripts and globals.
    Returns (name, scripts, globals, error) where scripts is [(id, text)] and
    globals is [(id, value_str)]."""
    name, path = job
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix="es_")
        os.close(fd)
        proc = subprocess.run(
            [TES3CONV, "-o", path, tmp],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, creationflags=_NO_WINDOW,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            return (name, [], [], err[-1] if err else f"tes3conv exit {proc.returncode}")
        with open(tmp, encoding="utf-8") as f:
            records = json.load(f)
    except Exception as e:
        return (name, [], [], f"{type(e).__name__}: {e}")
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    scripts, globals_ = [], []
    for r in records:
        t = r.get("type")
        if t == "Script":
            sid, text = r.get("id"), r.get("text")
            if sid and text:
                scripts.append((sid, text))
        elif t == "GlobalVariable":
            gid = r.get("id")
            if gid:
                globals_.append((gid, format_global(r.get("value"))))
    return (name, scripts, globals_, None)


def write_plugin(out_root, plugin, scripts, globals_, want_scripts, want_globals):
    """Write one plugin's folder. Returns (scripts_written, globals_written)."""
    folder = os.path.join(out_root, plugin)
    ns = ng = 0

    if want_scripts and scripts:
        os.makedirs(folder, exist_ok=True)
        for sid, text in scripts:
            safe = _UNSAFE.sub("_", sid)
            # newline="" keeps tes3conv's \r\n verbatim (no doubling, no stripping)
            with open(os.path.join(folder, safe + ".txt"), "w",
                      encoding="utf-8", newline="") as f:
                f.write(text)
            ns += 1

    if want_globals and globals_:
        os.makedirs(folder, exist_ok=True)
        lines = sorted(f"{gid} = {val}" for gid, val in globals_)
        with open(os.path.join(folder, "globals.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        ng = len(lines)

    return ns, ng


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if len(argv) < 1:
        print("usage: extract_scripts.py -- <input_folder>")
        sys.exit(2)

    root = argv[0]
    if not os.path.isdir(root):
        print(f"ERROR: input folder does not exist:\n  {root}")
        sys.exit(1)
    if not os.path.isfile(TES3CONV):
        print(f"ERROR: tes3conv.exe not found in the Shared folder:\n  {TES3CONV}")
        sys.exit(2)

    settings = load_settings()
    want_scripts = settings.get("include_scripts", "true").strip().lower() in _TRUE
    want_globals = settings.get("include_globals", "true").strip().lower() in _TRUE
    plugins_mode = settings.get("plugins", "enabled").strip().lower()
    if plugins_mode not in ("enabled", "all"):
        plugins_mode = "enabled"
    try:
        workers = int(settings.get("workers", "-1"))
    except ValueError:
        workers = -1
    if workers <= 0:
        workers = os.cpu_count() or 1

    mo2 = os.path.isfile(os.path.join(root, "ModOrganizer.ini"))
    missing = []
    if mo2:
        profile, mod_count, jobs, missing, source = collect_mo2_active(root, plugins_mode)
        print(f"MO2 instance detected (profile '{profile}', {mod_count} enabled mods).")
        print(f"Resolved {len(jobs)} plugin(s) from {source}"
              + (f"; {len(missing)} listed plugin(s) not found on disk." if missing else "."))
    else:
        jobs = collect_folder(root)
        print(f"Folder mode: {len(jobs)} plugin(s) found.")

    if not jobs:
        print("Nothing to do.")
        return

    output_dir = os.path.join(SCRIPT_DIR, "output")
    os.makedirs(output_dir, exist_ok=True)

    # Run tes3conv per plugin concurrently (I/O-bound on the subprocess). Threads,
    # not processes: the console-disturbance is handled by the bat's popup either
    # way, and threads keep the merge simple. Results are gathered in load order.
    started = time.time()
    results = [None] * len(jobs)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(extract_plugin, job): i for i, job in enumerate(jobs)}
        # collect as they finish, but store by original index to preserve order
        for fut in as_completed(futures):
            i = futures[fut]
            results[i] = fut.result()
            done += 1
            if done % 10 == 0 or done == len(jobs):
                print(f"\r  {done}/{len(jobs)} plugin(s)  ({time.time() - started:.0f}s)   ",
                      end="", flush=True)
    print()

    failed = [(r[0], r[3]) for r in results if r and r[3]]

    # Assemble per-plugin buckets. MO2 mode: winner-takes-all across the load
    # order (last definition of each id wins, attributed to that plugin). Folder
    # mode: every plugin keeps its own records, no cross-plugin dedup.
    per_scripts = defaultdict(dict)   # plugin -> {id_lower: (id, text)}
    per_globals = defaultdict(dict)   # plugin -> {id_lower: (id, val)}
    if mo2:
        script_win, global_win = {}, {}   # id_lower -> (plugin, id, payload)
        for name, scripts, globals_, err in results:
            if err:
                continue
            for sid, text in scripts:
                script_win[sid.lower()] = (name, sid, text)
            for gid, val in globals_:
                global_win[gid.lower()] = (name, gid, val)
        for plugin, sid, text in script_win.values():
            per_scripts[plugin][sid.lower()] = (sid, text)
        for plugin, gid, val in global_win.values():
            per_globals[plugin][gid.lower()] = (gid, val)
    else:
        for name, scripts, globals_, err in results:
            if err:
                continue
            for sid, text in scripts:
                per_scripts[name][sid.lower()] = (sid, text)
            for gid, val in globals_:
                per_globals[name][gid.lower()] = (gid, val)

    plugins = sorted(set(per_scripts) | set(per_globals), key=str.lower)
    total_scripts = total_globals = 0
    for plugin in plugins:
        s = list(per_scripts.get(plugin, {}).values())
        g = list(per_globals.get(plugin, {}).values())
        ns, ng = write_plugin(output_dir, plugin, s, g, want_scripts, want_globals)
        total_scripts += ns
        total_globals += ng

    print(f"\nWrote {len(plugins)} plugin folder(s) -> {output_dir}")
    print(f"  scripts : {total_scripts}")
    print(f"  globals : {total_globals}")
    if failed:
        print(f"  failed  : {len(failed)} plugin(s)")
        for name, err in failed[:10]:
            print(f"     {name}: {err}")
    if missing:
        print(f"  unresolved from load order : {len(missing)} (not found on disk)")


if __name__ == "__main__":
    main()
