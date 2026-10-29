#!/usr/bin/env python3
"""Generate the JLC_Basic_* KiCad symbol and footprint libraries.

Inputs:
  data/jlc_basic_parts.json   snapshot of the JLCPCB Basic list (--refresh re-fetches it)
  data/mapped_parts.toml      curated symbol/footprint mapping for non R/C-chip parts
  KiCad stock libraries       symbols and footprints are copied from there
  4ms-kicad-lib               resistor/capacitor chip footprints

Outputs (all regenerated from scratch on every run):
  JLC_Basic_{Resistors,Capacitors,Other}.kicad_sym
  JLC_Basic_{Resistors,Capacitors,Other}.pretty/
  datasheets/<LCSC>_<MPN>.pdf (downloaded only if missing)
"""

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jlc_api  # noqa: E402
from kisexpr import QStr, dump, find, find_all, get_property, load, walk  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(ROOT, "data", "jlc_basic_parts.json")
MAPPING = os.path.join(ROOT, "data", "mapped_parts.toml")
DATASHEET_DIR = "datasheets"
DATASHEET_VAR = "${JLC_BASIC_DIR}"

LIBS = {
    "resistors": "JLC_Basic_Resistors",
    "capacitors": "JLC_Basic_Capacitors",
    "other": "JLC_Basic_Other",
}

RESISTOR_CATEGORY = "Chip Resistor - Surface Mount"
MLCC_CATEGORY = "Multilayer Ceramic Capacitors MLCC - SMD/SMT"

# Chip R/C footprints come from the 4ms library (their hand-solder pads),
# keyed by package: (4ms library, 4ms footprint, KiCad 3D model)
FOURMS_FOOTPRINTS = {
    "R": {
        "0402": ("4ms_Resistor", "R_0402", "Resistor_SMD.3dshapes/R_0402_1005Metric.step"),
        "0603": ("4ms_Resistor", "R_0603", "Resistor_SMD.3dshapes/R_0603_1608Metric.step"),
        "0805": ("4ms_Resistor", "R_0805_2012Metric", "Resistor_SMD.3dshapes/R_0805_2012Metric.step"),
        "1206": ("4ms_Resistor", "R_1206_3216Metric", "Resistor_SMD.3dshapes/R_1206_3216Metric.step"),
    },
    "C": {
        "0402": ("4ms_Capacitor", "C_0402", "Capacitor_SMD.3dshapes/C_0402_1005Metric.step"),
        "0603": ("4ms_Capacitor", "C_0603", "Capacitor_SMD.3dshapes/C_0603_1608Metric.step"),
        "0805": ("4ms_Capacitor", "C_0805", "Capacitor_SMD.3dshapes/C_0805_2012Metric.step"),
        "1206": ("4ms_Capacitor", "C_1206", "Capacitor_SMD.3dshapes/C_1206_3216Metric.step"),
    },
}

TEXT_GAP = 0.2  # mm between the courtyard and the reference/value text


# --------------------------------------------------------------------------- naming

def resistance_token(r):
    """'4.7kΩ' -> '4.7k', '100Ω' -> '100R', '0Ω' -> '0R', '100mΩ' -> '100mR'."""
    t = r.replace("Ω", "").strip()
    return t + "R" if t[-1].isdigit() or t.endswith("m") else t


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9.+-]+", "_", s).strip("_")


def datasheet_filename(part):
    return f"{part['lcsc']}_{safe_name(part['mpn'])}.pdf"


# ------------------------------------------------------------------------ symbols

class StockSymbols:
    def __init__(self, path):
        self.path = path
        self.libs = {}

    def get(self, ref):
        lib, name = ref.split(":", 1)
        if lib not in self.libs:
            tree = load(os.path.join(self.path, f"{lib}.kicad_sym"))
            self.libs[lib] = {s[1]: s for s in find_all(tree, "symbol")}
        return self.libs[lib][name]

    def flattened(self, ref, new_name):
        """Copy of a stock symbol with any `extends` resolved and renamed to new_name."""
        lib = ref.split(":", 1)[0]
        sym = self.get(ref)
        root = sym
        while find(root, "extends"):
            root = self.get(f"{lib}:{find(root, 'extends')[1]}")
        out = copy.deepcopy(root)
        old = out[1]
        out[1] = QStr(new_name)
        for sub in find_all(out, "symbol"):
            sub[1] = QStr(new_name + sub[1][len(old):])
        if sym is not root:
            # the derived symbol's own properties replace the root's
            out[:] = [c for c in out if not (isinstance(c, list) and c[0] == "property")]
            idx = next(i for i, c in enumerate(out) if isinstance(c, list) and c[0] == "symbol")
            out[idx:idx] = copy.deepcopy(find_all(sym, "property"))
        return out


def make_property(name, value, at=(0, 0), hidden=True):
    p = ["property", QStr(name), QStr(value), ["at", str(at[0]), str(at[1]), "0"],
         ["show_name", "no"], ["do_not_autoplace", "no"]]
    if hidden:
        p.append(["hide", "yes"])
    p.append(["effects", ["font", ["size", "1.27", "1.27"]]])
    return p


def set_property(sym, name, value, hidden=True):
    p = get_property(sym, name)
    if p is not None:
        p[2] = QStr(value)
        return
    idx = max(i for i, c in enumerate(sym) if isinstance(c, list) and c[0] == "property") + 1
    sym.insert(idx, make_property(name, value, hidden=hidden))


def remove_property(sym, name):
    sym[:] = [c for c in sym if not (isinstance(c, list) and c[0] == "property" and c[1] == name)]


def remap_pins(sym, mapping):
    for node in walk(sym):
        if node and node[0] == "pin":
            num = find(node, "number")
            if num[1] in mapping:
                num[1] = QStr(mapping[num[1]])


def symbol_pins(sym):
    return {find(n, "number")[1] for n in walk(sym) if n and n[0] == "pin"}


def box_symbol(name, spec):
    """A plain rectangle symbol with pins on its sides."""
    font = ["effects", ["font", ["size", "1.27", "1.27"]]]
    g = 2.54  # every pin end lands on this grid
    left, right = spec.get("left", []), spec.get("right", [])
    top, bottom = spec.get("top", []), spec.get("bottom", [])
    rows = max(len(left), len(right), 1)
    cols = max(len(top), len(bottom), 1)
    ys = [g * ((rows - 1) // 2 - i) for i in range(rows)]
    xs = [g * (i - (cols - 1) // 2) for i in range(cols)]

    def text_len(names):  # rough rendered length of the longest pin name, mm
        return max([len(n) for n in names] + [0]) * 1.1

    def up(v):
        return g * -(-v // g)

    side_names = text_len(p[1] for p in left) + text_len(p[1] for p in right)
    half_w = up(max(side_names / 2 + g, max(abs(x) for x in xs) + g, 3 * g))
    top_y = up(max(ys) + g + (text_len(p[1] for p in top) if top else 0))
    bottom_y = -up(-min(ys) + g + (text_len(p[1] for p in bottom) if bottom else 0))
    half_h = top_y  # for the property placement below

    def pin(p, x, y, angle):
        num, pname, etype = p
        return ["pin", etype, "line", ["at", f"{x:g}", f"{y:g}", str(angle)], ["length", f"{g:g}"],
                ["name", QStr(pname), copy.deepcopy(font)], ["number", QStr(num), copy.deepcopy(font)]]

    pins = []
    pins += [pin(p, -half_w - g, y, 0) for p, y in zip(left, ys)]
    pins += [pin(p, half_w + g, y, 180) for p, y in zip(right, ys)]
    pins += [pin(p, x, top_y + g, 270) for p, x in zip(top, xs)]
    pins += [pin(p, x, bottom_y - g, 90) for p, x in zip(bottom, xs)]
    ref = spec.get("ref", "U")
    return [
        "symbol", QStr(name),
        ["pin_names", ["offset", "1.016"]],
        ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"],
        ["in_pos_files", "yes"], ["duplicate_pin_numbers_are_jumpers", "no"],
        make_property("Reference", ref, (-half_w, half_h + 1.27), hidden=False),
        make_property("Value", name, (half_w, half_h + 1.27), hidden=False),
        make_property("Footprint", ""), make_property("Datasheet", ""),
        make_property("Description", ""),
        ["symbol", QStr(f"{name}_0_1"),
         ["rectangle", ["start", f"{-half_w:g}", f"{top_y:g}"], ["end", f"{half_w:g}", f"{bottom_y:g}"],
          ["stroke", ["width", "0.254"], ["type", "default"]], ["fill", ["type", "background"]]]],
        ["symbol", QStr(f"{name}_1_1"), *pins],
        ["embedded_fonts", "no"],
    ]


# ----------------------------------------------------------------------- footprints

def _points(node):
    for n in walk(node):
        if n and n[0] in ("start", "end", "xy", "mid", "center") and len(n) >= 3:
            yield float(n[1]), float(n[2])


def courtyard_y(fp):
    ys = []
    for n in fp:
        if isinstance(n, list) and n[0] in ("fp_line", "fp_rect", "fp_poly", "fp_arc", "fp_circle"):
            layer = find(n, "layer")
            if layer and layer[1] == "F.CrtYd":
                if n[0] == "fp_circle":
                    (cx, cy), (ex, ey) = find(n, "center")[1:3], find(n, "end")[1:3]
                    r = ((float(ex) - float(cx)) ** 2 + (float(ey) - float(cy)) ** 2) ** 0.5
                    ys += [float(cy) - r, float(cy) + r]
                else:
                    ys += [y for _, y in _points(n)]
    if not ys:  # fall back to the pads
        for pad in find_all(fp, "pad"):
            at, size = find(pad, "at"), find(pad, "size")
            ys += [float(at[2]) - float(size[2]) / 2, float(at[2]) + float(size[2]) / 2]
    return min(ys), max(ys)


def _font_height(prop):
    size = next((n for n in walk(prop) if n and n[0] == "size"), None)
    return float(size[1]) if size else 1.0


def _center_text(node, x, y):
    at = find(node, "at")
    at[1:] = [f"{x:g}", f"{y:g}", "0"]
    eff = find(node, "effects")
    if eff:
        eff[:] = [c for c in eff if not (isinstance(c, list) and c[0] == "justify")]


def center_texts(fp):
    """Reference centered above the courtyard, value centered below, fab ref on the origin."""
    top, bottom = courtyard_y(fp)
    ref = get_property(fp, "Reference")
    val = get_property(fp, "Value")
    _center_text(ref, 0, round(top - TEXT_GAP - _font_height(ref) / 2, 3))
    _center_text(val, 0, round(bottom + TEXT_GAP + _font_height(val) / 2, 3))
    for t in find_all(fp, "fp_text"):
        if t[1] == "user" and t[2] in ("${REFERENCE}", "%R"):
            angle = find(t, "at")[3] if len(find(t, "at")) > 3 else "0"
            _center_text(t, 0, 0)
            find(t, "at")[3] = angle


def footprint_pads(fp):
    return {p[1] for p in find_all(fp, "pad") if p[1] != ""}


def finish_footprint(fp, name, model=None):
    fp[1] = QStr(name)
    get_property(fp, "Reference")[2] = QStr("REF**")
    get_property(fp, "Value")[2] = QStr(name)
    center_texts(fp)
    if model:
        fp[:] = [c for c in fp if not (isinstance(c, list) and c[0] == "model")]
        fp.append(["model", QStr("${KICAD10_3DMODEL_DIR}/" + model),
                   ["offset", ["xyz", "0", "0", "0"]], ["scale", ["xyz", "1", "1", "1"]],
                   ["rotate", ["xyz", "0", "0", "0"]]])
    for m in find_all(fp, "model"):
        m[1] = QStr(re.sub(r"\$\{KICAD\d+_3DMODEL_DIR\}", "${KICAD10_3DMODEL_DIR}", m[1]))
    return fp


def load_4ms_footprints(fourms_dir, workdir):
    """Upgrade the needed 4ms R/C footprints to the current format; returns {(kind, pkg): tree}."""
    src = os.path.join(workdir, "in.pretty")
    dst = os.path.join(workdir, "out.pretty")
    os.makedirs(src)
    for kind, pkgs in FOURMS_FOOTPRINTS.items():
        for lib, fp, _ in pkgs.values():
            shutil.copy(os.path.join(fourms_dir, f"{lib}.pretty", f"{fp}.kicad_mod"), src)
    subprocess.run(["kicad-cli", "fp", "upgrade", "--force", "-o", dst, src],
                   check=True, capture_output=True)
    out = {}
    for kind, pkgs in FOURMS_FOOTPRINTS.items():
        for pkg, (lib, fp, model) in pkgs.items():
            out[(kind, pkg)] = (load(os.path.join(dst, f"{fp}.kicad_mod")), model)
    return out


# --------------------------------------------------------------------------- parts

def describe(part, prefix):
    tail = re.sub(r"\s*ROHS\s*$", "", part["description"])
    return f"{prefix} ({part['manufacturer']} {part['mpn']}). {tail}"


def chip_resistor(part):
    a = part["attributes"]
    tok = resistance_token(a["Resistance"])
    pkg = part["package"]
    return {
        "lib": "resistors", "name": f"R_{tok}_{pkg}", "value": tok,
        "symbol": "Device:R", "chip": ("R", pkg),
        "description": describe(part, f"{a['Resistance']} {a['Tolerance']} {a['Power(Watts)']} {pkg} chip resistor"),
        "extra": {"Tolerance": a["Tolerance"], "Power": a["Power(Watts)"]},
    }


def mlcc(part):
    a = part["attributes"]
    cap, volt, diel, pkg = a["Capacitance"], a["Voltage Rating"], a["Temperature Coefficient"], part["package"]
    return {
        "lib": "capacitors", "name": f"C_{cap}_{volt}_{diel}_{pkg}", "value": cap,
        "symbol": "Device:C", "chip": ("C", pkg),
        "description": describe(part, f"{cap} {volt} {diel} {a['Tolerance']} {pkg} MLCC"),
        "extra": {"Voltage": volt, "Dielectric": diel, "Tolerance": a["Tolerance"]},
    }


def mapped(part, entry):
    e = dict(entry)
    e["description"] = describe(part, f"{part['mpn']} {part['category']}, {part['package']}")
    e["extra"] = {}
    if e["lib"] == "capacitors":
        a = part["attributes"]
        e["extra"] = {"Voltage": a.get("Voltage Rating", ""), "Dielectric": "Tantalum",
                      "Tolerance": a.get("Tolerance", "")}
    return e


def plan(parts, mapping):
    planned, missing = [], []
    for part in parts:
        if part["lcsc"] in mapping:
            e = mapped(part, mapping[part["lcsc"]])
        elif part["category"] == RESISTOR_CATEGORY:
            e = chip_resistor(part)
        elif part["category"] == MLCC_CATEGORY:
            e = mlcc(part)
        else:
            missing.append(part)
            continue
        e["part"] = part
        planned.append(e)
    return planned, missing


# ---------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-fetch the Basic list from JLCPCB first")
    ap.add_argument("--no-download", action="store_true", help="don't download missing datasheets")
    ap.add_argument("--kicad-symbols", default=os.environ.get("KICAD10_SYMBOL_DIR", "/usr/share/kicad/symbols"))
    ap.add_argument("--kicad-footprints", default=os.environ.get("KICAD10_FOOTPRINT_DIR", "/usr/share/kicad/footprints"))
    ap.add_argument("--fourms", default=os.path.join(ROOT, "..", "4ms-kicad-lib", "footprints"),
                    help="4ms-kicad-lib footprints directory")
    args = ap.parse_args()

    if args.refresh:
        parts = jlc_api.fetch_basic_parts()
        with open(SNAPSHOT, "w", encoding="utf-8") as f:
            json.dump(parts, f, indent=1, ensure_ascii=False)
    with open(SNAPSHOT, encoding="utf-8") as f:
        parts = json.load(f)
    with open(MAPPING, "rb") as f:
        mapping = tomllib.load(f)

    planned, missing = plan(parts, mapping)
    if missing:
        print("These Basic parts have no entry in data/mapped_parts.toml:", file=sys.stderr)
        for p in missing:
            print(f"  {p['lcsc']} {p['mpn']} [{p['category']}] {p['package']}", file=sys.stderr)
        sys.exit(1)
    stale = sorted(set(mapping) - {p["lcsc"] for p in parts})
    if stale:
        print(f"note: mapped_parts.toml has entries no longer in the Basic list: {', '.join(stale)}")

    names = {}
    for e in planned:
        key = (e["lib"], e["name"])
        if key in names:
            sys.exit(f"duplicate symbol name {e['name']}: {names[key]} and {e['part']['lcsc']}")
        names[key] = e["part"]["lcsc"]

    stock = StockSymbols(args.kicad_symbols)
    footprints = {lib: {} for lib in LIBS}
    symbols = {lib: [] for lib in LIBS}
    errors = []

    with tempfile.TemporaryDirectory() as tmp:
        chips = load_4ms_footprints(args.fourms, tmp)

        for e in sorted(planned, key=lambda e: e["name"]):
            part, lib = e["part"], e["lib"]
            if e["symbol"] == "box":
                sym = box_symbol(e["name"], e["box"])
            else:
                sym = stock.flattened(e["symbol"], e["name"])
            if e.get("pins"):
                remap_pins(sym, e["pins"])

            # footprint
            if "chip" in e:
                kind, pkg = e["chip"]
                tree, model = chips[(kind, pkg)]
                fp_name = f"{kind}_{pkg}"
                if fp_name not in footprints[lib]:
                    footprints[lib][fp_name] = finish_footprint(copy.deepcopy(tree), fp_name, model)
            else:
                fp_ref = e.get("footprint") or get_property(stock.get(e["symbol"]), "Footprint")[2]
                if not fp_ref:
                    errors.append(f"{part['lcsc']}: no footprint given")
                    continue
                fp_lib, fp_name = fp_ref.split(":", 1)
                if fp_name not in footprints[lib]:
                    tree = load(os.path.join(args.kicad_footprints, f"{fp_lib}.pretty", f"{fp_name}.kicad_mod"))
                    footprints[lib][fp_name] = finish_footprint(tree, fp_name)

            pins, pads = symbol_pins(sym), footprint_pads(footprints[lib][fp_name])
            if pins != pads:
                errors.append(f"{part['lcsc']} {e['name']}: symbol pins {sorted(pins)} != footprint pads {sorted(pads)}")

            # datasheet
            ds_file = datasheet_filename(part)
            ds_path = os.path.join(ROOT, DATASHEET_DIR, ds_file)
            if not os.path.exists(ds_path) and not args.no_download:
                os.makedirs(os.path.dirname(ds_path), exist_ok=True)
                jlc_api.download_datasheet(part, ds_path)
            datasheet = f"{DATASHEET_VAR}/{DATASHEET_DIR}/{ds_file}" if os.path.exists(ds_path) else part["datasheet_url"]

            set_property(sym, "Value", e["value"])
            set_property(sym, "Footprint", f"{LIBS[lib]}:{fp_name}")
            set_property(sym, "Datasheet", datasheet)
            set_property(sym, "Description", e["description"])
            remove_property(sym, "ki_fp_filters")
            set_property(sym, "ki_keywords", f"JLCPCB basic {part['lcsc']} {part['mpn']} {part['category']}")
            set_property(sym, "LCSC", part["lcsc"])
            set_property(sym, "JLCPCB Part #", part["lcsc"])
            set_property(sym, "Part #", part["mpn"])
            set_property(sym, "Manufacturer", part["manufacturer"])
            set_property(sym, "Datasheet URL", part["datasheet_url"])
            for k, v in e["extra"].items():
                set_property(sym, k, v)
            symbols[lib].append(sym)

    if errors:
        print("\n".join(errors), file=sys.stderr)
        sys.exit(1)

    for key, libname in LIBS.items():
        dump(["kicad_symbol_lib", ["version", "20251024"], ["generator", QStr("jlc_basic_generate_libs")],
              ["generator_version", QStr("10.0")], *symbols[key]],
             os.path.join(ROOT, f"{libname}.kicad_sym"))
        pretty = os.path.join(ROOT, f"{libname}.pretty")
        if os.path.isdir(pretty):
            shutil.rmtree(pretty)
        os.makedirs(pretty)
        for fp_name, tree in sorted(footprints[key].items()):
            dump(tree, os.path.join(pretty, f"{fp_name}.kicad_mod"))
        print(f"{libname}: {len(symbols[key])} symbols, {len(footprints[key])} footprints")


if __name__ == "__main__":
    main()
