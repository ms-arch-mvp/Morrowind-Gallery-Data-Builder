import json
import sys

# --------------------------------------------------------------------------
# Record-filtering policy (edit here; operational settings are in config.ini)
# --------------------------------------------------------------------------
included_types = [
    "Activator",
    "Alchemy",
    "Apparatus",
    "Armor",
    "Bodypart",
    "Book",
    "Clothing",
    "Container",
    "Creature",
    "Door",
    "Enchanting",
    "Ingredient",
    "LeveledItem",
    "LeveledCreature",
    "Light",
    "MiscItem",
    "Npc",
    "RepairItem",
    "Spell",
    "Static",
    "Weapon",
]

npc_requires_respawn = True

excluded_ids = [
    "Imperial Guard_prisoner",
    "Imperial Guard_M_Sadri",
    "ordinator_MH_Sadri",
    "ordinator_high fane",
    "ordinator_wander_hvault",
    "ordinator_wander_tvault",
]


def keep_entry(x):
    if not isinstance(x, dict) or x.get("type") not in included_types:
        return False
    if x.get("id") in excluded_ids:
        return False
    if x.get("type") == "Npc":
        npc_id = x.get("id", "")
        if isinstance(npc_id, str) and "outfit" in npc_id.lower():
            return True
        if npc_requires_respawn:
            flags = x.get("npc_flags", "")
            if not (isinstance(flags, str) and "RESPAWN" in flags):
                return False
        script = x.get("script", "")
        return not (isinstance(script, str) and script != "")
    if x.get("type") == "Bodypart":
        d = x.get("data", {})
        return d.get("part") in ("Head", "Hair") and d.get("bodypart_type") == "Skin"
    return True


def filter_json(infile, outfile):
    """Filter one tes3conv JSON; returns (kept, total) or None on bad structure."""
    with open(infile, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        print("  Unexpected JSON structure: top level is not a list.")
        return None
    filtered = [x for x in data if keep_entry(x)]
    for x in filtered:
        if x.get("type") != "Npc":
            x.pop("text", None)
        if isinstance(x.get("mesh"), str):
            x["mesh"] = x["mesh"].lower()
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(filtered, f, indent=2)
    return len(filtered), len(data)


def main():
    if len(sys.argv) != 3:
        print("usage: build_gallery_data.py <input.json> <output.json>")
        sys.exit(1)
    infile, outfile = sys.argv[1], sys.argv[2]
    res = filter_json(infile, outfile)
    if res:
        print(f"  kept {res[0]} of {res[1]} entries")


if __name__ == "__main__":
    main()
