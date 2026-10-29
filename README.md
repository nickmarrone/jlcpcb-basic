# JLCPCB Basic parts for KiCad

KiCad 10 symbol and footprint libraries holding every part in JLCPCB's **Basic**
library (no per-part setup fee for PCBA), plus a script that compares the
libraries against the live JLCPCB list.

| Library | Contents |
|---|---|
| `JLC_Basic_Resistors` | chip resistors, resistor arrays |
| `JLC_Basic_Capacitors` | MLCCs, tantalum capacitors |
| `JLC_Basic_Other` | everything else (diodes, LEDs, transistors, regulators, ICs, crystals, switches, ...) |

Each library is a `.kicad_sym` file with a matching `.pretty` footprint folder.
Datasheets for all parts are in `datasheets/`.

## Setup

1. In KiCad, **Preferences → Configure Paths**: add `JLC_BASIC_DIR` pointing at this
   directory. The symbols' Datasheet fields use it (`${JLC_BASIC_DIR}/datasheets/...`).
2. Add the libraries from `lib-tables/sym-lib-table` and `lib-tables/fp-lib-table` to your
   global or project library tables. The symbols expect the footprint libraries to be
   named exactly `JLC_Basic_Resistors`, `JLC_Basic_Capacitors` and `JLC_Basic_Other`.

## Naming

Every symbol name includes the package.

- Resistors: `R_<value>_<package>`, e.g. `R_10k_0603`, `R_100R_0402`, `R_0R_0805`;
  arrays: `R_Array_4x10k_0603x4`
- Capacitors: `C_<capacitance>_<voltage>_<dielectric>_<package>`, e.g.
  `C_100nF_50V_X7R_0603`, `C_100uF_6.3V_Tantalum_3528`
- Other: `<MPN>_<package>` (`AMS1117-3.3_SOT-223`, `LM358_SOIC-8`) or a generic
  name for commodity parts (`LED_Red_0603`, `Crystal_16MHz_3225`, `FB_600R_0603`)

## Symbol fields

| Field | Contents |
|---|---|
| `LCSC`, `JLCPCB Part #` | LCSC/JLCPCB part number (e.g. `C25804`) |
| `Part #`, `Manufacturer` | manufacturer part number and manufacturer |
| `Datasheet` | local PDF in `datasheets/` |
| `Datasheet URL` | LCSC datasheet link |
| `Voltage`, `Dielectric`, `Tolerance`, `Power` | on resistors/capacitors |

## Footprints

- Chip resistors and capacitors (0402–1206) use the 4ms library footprints
  (`4ms_Resistor`/`4ms_Capacitor`, larger pads for hand soldering), converted to
  KiCad 10 format and pointed at KiCad's 3D models.
- Tantalum capacitors use KiCad's `_HandSolder` variants.
- Everything else uses KiCad stock footprints.
- On every footprint, the reference is centered horizontally just above the courtyard,
  the value is centered just below it (F.Fab), and the fab-layer reference sits on the
  origin, so nothing needs moving after placement.

Pin numbering follows KiCad convention: on diodes and LEDs, pad 1 is the cathode.

## Checking for JLCPCB changes

```sh
python3 tools/check_updates.py -o report.md
```

This fetches the live Basic list and reports parts **added** to Basic (with category,
package, attributes and datasheet link), parts **removed** from Basic, and LCSC numbers
whose MPN changed. The report ends with step-by-step instructions, so it can be pasted
into an LLM as-is. Use `--json` for machine-readable output, or `--snapshot file.json` to
compare against a saved list.

## Regenerating

The `.kicad_sym` and `.pretty` files are generated. Change the inputs instead:

- `data/jlc_basic_parts.json`: snapshot of the Basic list (`--refresh` re-fetches it)
- `data/mapped_parts.toml`: symbol/footprint/name for each part that isn't a plain chip
  resistor or MLCC (those are built by rule)

```sh
python3 tools/generate_libs.py            # rebuild from the snapshot
python3 tools/generate_libs.py --refresh  # fetch the live list, then rebuild
```

Requirements: Python 3.11+, `kicad-cli` (KiCad 10), KiCad's stock libraries (default
`/usr/share/kicad/{symbols,footprints}`; override with `--kicad-symbols`/`--kicad-footprints`),
and `../4ms-kicad-lib` (override with `--fourms`). Missing datasheets are downloaded.

The generator fails if a Basic part has no mapping, if two parts get the same name, or
if a symbol's pin numbers don't match its footprint's pad numbers.

## How the pinouts were checked

Every non-passive part's pin numbers were compared with JLC/EasyEDA's own symbol for
that LCSC number. Where KiCad's stock symbol disagreed, the part uses a remapped or
generic symbol, as recorded in the `verified` notes in `data/mapped_parts.toml`:

- `S8050`/`SS8050`/`S8550`/`SS8550`: these SOT-23 parts are B-E-C, while KiCad's stock
  symbols for them are E-B-C (TO-92), so they use the generic `Q_*_BEC` symbols
- `CJ431`: SOT-23 is 1=REF 2=K 3=A, which differs from TI's TL431DBZ
- `MB10S`: 1=+ 2=− 3,4=AC, which differs from KiCad's MB2S
