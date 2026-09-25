"""Parsers for sky130 liberty, Yosys JSON netlists and OpenSTA path reports."""
import json, re

_HDR = re.compile(r'(\w+)\s*\(\s*"?([^",)]*)[^{]*\{')

def load_liberty(path):
    cells, stack, cell, pin = {}, [], None, None
    for line in open(path):
        s = line.strip()
        m = _HDR.match(s)
        if m:
            kind, name = m.groups()
            stack.append(kind)
            stack.extend(["?"] * (s.count("{") - 1))
            if kind == "cell" and len(stack) == 2:
                cell = cells.setdefault(name, {"inputs": [], "outputs": {}, "area": 0.0, "seq": False})
            elif kind == "pin" and cell is not None and stack[-2] == "cell":
                pin = name
            elif kind in ("ff", "latch", "statetable") and cell is not None:
                cell["seq"] = True
        elif cell is not None and pin and stack[-1] == "pin":
            d = re.match(r'direction\s*:\s*"?(\w+)', s)
            f = re.match(r'function\s*:\s*"([^"]*)"', s)
            if d and d.group(1) == "input":
                cell["inputs"].append(pin)
            elif d and d.group(1) == "output":
                cell["outputs"].setdefault(pin, None)
            if f:
                cell["outputs"][pin] = f.group(1)
        elif cell is not None and stack and stack[-1] == "cell":
            a = re.match(r'area\s*:\s*([\d.]+)', s)
            if a:
                cell["area"] = float(a.group(1))
        elif s.count("{"):
            stack.extend(["?"] * s.count("{"))
        for _ in range(s.count("}")):
            kind = stack.pop()
            if kind == "pin":
                pin = None
            elif kind == "cell":
                cell = None
    return cells

_FN_OK = re.compile(r"[\w\s!&|^()*+]+")

def eval_fn(expr, env):
    if not _FN_OK.fullmatch(expr):
        raise ValueError(f"unsupported liberty function: {expr}")
    py = expr.replace("!", " ~").replace("*", "&").replace("+", "|")
    return eval(py, {"__builtins__": {}}, dict(env)) & 1

def load_netlist(json_path, lib):
    d = json.load(open(json_path))
    top = next(m for m in d["modules"].values() if m["attributes"].get("top"))
    cells, driver, loads = {}, {}, {}
    for inst, c in top["cells"].items():
        pins = {p: b[0] for p, b in c["connections"].items() if b}
        cells[inst] = {"type": c["type"], "pins": pins}
        info = lib.get(c["type"], {"inputs": [], "outputs": {}})
        for p, bit in pins.items():
            if p in info["outputs"]:
                driver[bit] = (inst, p)
            elif p in info["inputs"]:
                loads.setdefault(bit, []).append((inst, p))
    for pname, port in top["ports"].items():
        if port["direction"] == "output":
            for bit in port["bits"]:
                loads.setdefault(bit, []).append(("$port", pname))
    return cells, driver, loads

_ROW = re.compile(r'^\s*([-\d.\s]+?)\s+[\^v]\s+(\S+)/(\S+)\s+\((\S+)\)\s*$')

def parse_paths(text):
    paths, rows, active = [], None, False
    for line in text.splitlines():
        if line.startswith("Startpoint:"):
            rows, active = [], True
        elif "data arrival time" in line:
            active = False
        elif "slack (" in line and rows is not None:
            paths.append({"slack": float(line.split()[0]), "rows": rows})
            rows = None
        elif active:
            m = _ROW.match(line)
            if m:
                nums = [float(x) for x in m.group(1).split()]
                rows.append({"inst": m.group(2), "pin": m.group(3), "cell": m.group(4),
                             "delay": nums[-2], "time": nums[-1],
                             "cap": nums[0] if len(nums) == 4 else None})
    return paths
