"""Minimal reader/writer for KiCad s-expression files (.kicad_sym, .kicad_mod).

Lists are Python lists, bare atoms are ``str`` and quoted strings are ``QStr``,
so a file round-trips without losing which atoms were quoted.
"""

import re


class QStr(str):
    """A string that is written back out quoted."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)


def parse(text):
    stack = [[]]
    pos = 0
    while True:
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        lparen, rparen, quoted, atom = m.groups()
        if lparen:
            stack.append([])
        elif rparen:
            done = stack.pop()
            stack[-1].append(done)
        elif quoted is not None:
            stack[-1].append(QStr(re.sub(r'\\(.)', r'\1', quoted)))
        else:
            stack[-1].append(atom)
    if text[pos:].strip():
        raise ValueError(f"unparsed trailing data at offset {pos}")
    if len(stack) != 1 or len(stack[0]) != 1:
        raise ValueError("unbalanced s-expression")
    return stack[0][0]


def load(path):
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def _atom(a):
    if isinstance(a, QStr):
        return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(a, (int, float)):
        return fmt_num(a)
    return a


def fmt_num(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# Lists whose children are written on one line, as KiCad does.
_INLINE = {"at", "xy", "size", "start", "end", "mid", "center", "offset", "scale",
           "rotate", "xyz", "font", "stroke", "fill", "width", "length", "layers",
           "version", "generator", "generator_version", "type", "color", "hide",
           "justify", "thickness", "uuid", "layer", "radius", "property_tag"}


def dumps(node, indent=0):
    if not isinstance(node, list):
        return _atom(node)
    head = node[0] if node else ""
    simple = all(not isinstance(c, list) for c in node)
    if simple or head in _INLINE:
        return "(" + " ".join(dumps(c) for c in node) + ")"
    # Leading atoms share the opening line; everything after goes one per line.
    n = 0
    while n < len(node) and not isinstance(node[n], list):
        n += 1
    out = "(" + " ".join(_atom(c) for c in node[:n])
    for c in node[n:]:
        out += "\n" + "\t" * (indent + 1) + dumps(c, indent + 1)
    return out + "\n" + "\t" * indent + ")"


def dump(node, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(node) + "\n")


# --- tree helpers -----------------------------------------------------------

def find(node, head):
    """First direct child list whose first atom is ``head``."""
    for c in node:
        if isinstance(c, list) and c and c[0] == head:
            return c
    return None


def find_all(node, head):
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def walk(node):
    yield node
    for c in node:
        if isinstance(c, list):
            yield from walk(c)


def get_property(node, name):
    for p in find_all(node, "property"):
        if p[1] == name:
            return p
    return None
