#!/usr/bin/env python3
"""Compare the JLC_Basic_* libraries against the live JLCPCB Basic parts list.

Prints a Markdown report (or JSON with --json) of parts added to or removed
from JLCPCB Basic since the libraries were generated, with enough detail per
part to paste into an LLM and have it update data/mapped_parts.toml.

  python3 tools/check_updates.py                 # report against live data
  python3 tools/check_updates.py -o report.md    # write the report to a file
  python3 tools/check_updates.py --snapshot f.json   # compare against a saved list instead
  python3 tools/check_updates.py --save-snapshot     # also store the live list as
                                                     # data/jlc_basic_parts.json
"""

import argparse
import json
import os
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jlc_api  # noqa: E402
from generate_libs import LIBS, MAPPING, MLCC_CATEGORY, RESISTOR_CATEGORY, ROOT, SNAPSHOT  # noqa: E402
from kisexpr import find_all, get_property, load  # noqa: E402


def library_parts():
    """{lcsc: {library, symbol, mpn, footprint}} for every symbol with an LCSC field."""
    parts = {}
    for libname in LIBS.values():
        path = os.path.join(ROOT, f"{libname}.kicad_sym")
        if not os.path.exists(path):
            continue
        for sym in find_all(load(path), "symbol"):
            lcsc = get_property(sym, "LCSC")
            if lcsc is None or not lcsc[2]:
                continue
            mpn = get_property(sym, "Part #")
            fp = get_property(sym, "Footprint")
            parts[lcsc[2]] = {
                "library": libname, "symbol": str(sym[1]),
                "mpn": mpn[2] if mpn else "", "footprint": fp[2] if fp else "",
            }
    return parts


def handling(part, mapping):
    if part["lcsc"] in mapping:
        return "already in data/mapped_parts.toml; regenerate"
    if part["category"] == RESISTOR_CATEGORY:
        return "automatic (chip resistor rule); regenerate"
    if part["category"] == MLCC_CATEGORY:
        return "automatic (MLCC rule); regenerate"
    return "NEEDS an entry in data/mapped_parts.toml"


def compare(live, lib, mapping):
    live_by = {p["lcsc"]: p for p in live}
    added = [dict(p, handling=handling(p, mapping)) for p in live if p["lcsc"] not in lib]
    removed = [dict(v, lcsc=k) for k, v in sorted(lib.items()) if k not in live_by]
    changed = []
    for lcsc, entry in sorted(lib.items()):
        p = live_by.get(lcsc)
        if p and entry["mpn"] and entry["mpn"] != p["mpn"]:
            changed.append({"lcsc": lcsc, "symbol": entry["symbol"], "library": entry["library"],
                            "library_mpn": entry["mpn"], "live_mpn": p["mpn"]})
    return {"live_count": len(live), "library_count": len(lib),
            "added": added, "removed": removed, "changed": changed}


INSTRUCTIONS = """\
## How to apply these changes

The libraries are generated; do not hand-edit the .kicad_sym/.pretty files.

1. Refresh the snapshot: `python3 tools/generate_libs.py --refresh` (or
   `python3 tools/check_updates.py --save-snapshot`).
2. Added chip resistors and MLCCs need nothing more; they are named and built by rule.
3. Every added part marked "NEEDS an entry" gets a table in `data/mapped_parts.toml`
   (see the header of that file and the existing entries for the format):
   pick a KiCad stock symbol whose pin numbers match the part's datasheet pinout
   (use `pins = {old = new}` to remap, or `symbol = "box"`), a KiCad stock footprint
   for the package, and a name that ends with the package (e.g. `LM358_SOIC-8`).
   Record how the pinout was checked in `verified`.
4. Delete the `data/mapped_parts.toml` tables of removed parts (the generator warns about them).
5. Run `python3 tools/generate_libs.py`; it fails if any symbol's pins don't match its
   footprint's pads, or if any Basic part is unmapped.
"""


def markdown(r):
    out = ["# JLCPCB Basic parts: library vs live", "",
           f"- Live Basic parts: {r['live_count']}",
           f"- Parts in the libraries: {r['library_count']}",
           f"- Added to Basic (not in libraries): {len(r['added'])}",
           f"- Removed from Basic (still in libraries): {len(r['removed'])}",
           f"- MPN changed: {len(r['changed'])}", ""]
    if r["added"]:
        out += ["## Added to JLCPCB Basic", ""]
        for p in r["added"]:
            out += [f"### {p['lcsc']} {p['mpn']}", "",
                    f"- Handling: {p['handling']}",
                    f"- Manufacturer: {p['manufacturer']}",
                    f"- Category: {p['parent_category']} / {p['category']}",
                    f"- Package: {p['package']}",
                    f"- Description: {p['description']}",
                    f"- Datasheet: {p['datasheet_url']}",
                    f"- JLCPCB: {p['jlc_url']}"]
            if p["attributes"]:
                out.append("- Attributes: " + "; ".join(f"{k}: {v}" for k, v in p["attributes"].items()))
            out.append("")
    if r["removed"]:
        out += ["## Removed from JLCPCB Basic", "",
                "| LCSC | Symbol | Library | MPN |", "|---|---|---|---|"]
        out += [f"| {p['lcsc']} | {p['symbol']} | {p['library']} | {p['mpn']} |" for p in r["removed"]]
        out.append("")
    if r["changed"]:
        out += ["## MPN changed for an existing LCSC number", "",
                "| LCSC | Symbol | Library MPN | Live MPN |", "|---|---|---|---|"]
        out += [f"| {p['lcsc']} | {p['symbol']} | {p['library_mpn']} | {p['live_mpn']} |" for p in r["changed"]]
        out.append("")
    if not (r["added"] or r["removed"] or r["changed"]):
        out += ["No differences: the libraries match the live JLCPCB Basic list.", ""]
    else:
        out.append(INSTRUCTIONS)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    ap.add_argument("-o", "--output", help="write the report to this file instead of stdout")
    ap.add_argument("--snapshot", help="compare against this saved parts JSON instead of fetching live")
    ap.add_argument("--save-snapshot", action="store_true",
                    help=f"write the live list to {os.path.relpath(SNAPSHOT, ROOT)}")
    args = ap.parse_args()

    if args.snapshot:
        with open(args.snapshot, encoding="utf-8") as f:
            live = json.load(f)
    else:
        live = jlc_api.fetch_basic_parts()
    if args.save_snapshot:
        with open(SNAPSHOT, "w", encoding="utf-8") as f:
            json.dump(live, f, indent=1, ensure_ascii=False)
    with open(MAPPING, "rb") as f:
        mapping = tomllib.load(f)

    report = compare(live, library_parts(), mapping)
    text = json.dumps(report, indent=1, ensure_ascii=False) if args.json else markdown(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"added {len(report['added'])}, removed {len(report['removed'])}, "
              f"changed {len(report['changed'])} -> {args.output}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
