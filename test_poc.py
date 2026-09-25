"""Assert-based self-check. Run: LIB=<sky130 hd tt lib> python3 test_poc.py"""
import os, json
import netlist

RPT = """Startpoint: u_1 (rising edge-triggered flip-flop clocked by clk)
Endpoint: u_9 (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

     Cap      Slew     Delay      Time   Description
-----------------------------------------------------------------------------
            0.0000    0.0000    0.0000   clock clk (rise edge)
                      0.0000    0.0000   clock network delay (ideal)
            0.0000    0.0000    0.0000 ^ u_1/CLK (sky130_fd_sc_hd__dfxtp_1)
  0.0050    0.0400    0.3000    0.3000 v u_1/Q (sky130_fd_sc_hd__dfxtp_1)
            0.0400    0.0000    0.3000 v u_2/A (sky130_fd_sc_hd__nand2_1)
  0.0040    0.0500    0.0800    0.3800 ^ u_2/Y (sky130_fd_sc_hd__nand2_1)
            0.0500    0.0000    0.3800 ^ u_9/D (sky130_fd_sc_hd__dfxtp_1)
                                0.3800   data arrival time

            0.0000    1.0000    1.0000   clock clk (rise edge)
                                1.0000 ^ u_9/CLK (sky130_fd_sc_hd__dfxtp_1)
                     -0.1000    0.9000   library setup time
                                0.9000   data required time
-----------------------------------------------------------------------------
                               -0.2000   slack (VIOLATED)
"""

def test_parsers():
    p = netlist.parse_paths(RPT)
    assert len(p) == 1 and p[0]["slack"] == -0.2
    names = [(r["inst"], r["pin"]) for r in p[0]["rows"]]
    assert names == [("u_1", "CLK"), ("u_1", "Q"), ("u_2", "A"), ("u_2", "Y"), ("u_9", "D")], names
    assert p[0]["rows"][3]["delay"] == 0.08 and p[0]["rows"][3]["cap"] == 0.004
    assert netlist.eval_fn("!((A1&A2) | B1)", {"A1": 1, "A2": 1, "B1": 0}) == 0
    assert netlist.eval_fn("A^B", {"A": 1, "B": 1}) == 0
    try:
        netlist.eval_fn("A'", {"A": 1}); assert False, "must reject"
    except ValueError:
        pass
    lib = netlist.load_liberty(os.environ["LIB"])
    a = lib["sky130_fd_sc_hd__a21oi_1"]
    assert sorted(a["inputs"]) == ["A1", "A2", "B1"] and "Y" in a["outputs"] and not a["seq"]
    assert lib["sky130_fd_sc_hd__dfxtp_1"]["seq"]

import mine

def test_mine_toy():
    lib = {"nand2": {"inputs": ["A", "B"], "outputs": {"Y": "!(A&B)"}, "seq": False, "area": 1},
           "inv":   {"inputs": ["A"], "outputs": {"Y": "!A"}, "seq": False, "area": 1}}
    # u1 = nand(a,b) -> u2 = inv -> port ; u3 = nand(a, u1)  => u1 has fanout 2: no (u1,u2) cluster
    cells = {"u1": {"type": "nand2", "pins": {"A": 1, "B": 2, "Y": 3}},
             "u2": {"type": "inv",   "pins": {"A": 3, "Y": 4}},
             "u3": {"type": "nand2", "pins": {"A": 1, "B": 3, "Y": 5}},
             "u4": {"type": "inv",   "pins": {"A": 5, "Y": 6}}}
    driver = {3: ("u1", "Y"), 4: ("u2", "Y"), 5: ("u3", "Y"), 6: ("u4", "Y")}
    loads = {1: [("u1", "A"), ("u3", "A")], 2: [("u1", "B")], 3: [("u2", "A"), ("u3", "B")],
             5: [("u4", "A")], 4: [("$port", "o1")], 6: [("$port", "o2")]}
    cl = mine.mine(cells, driver, loads, lib)
    assert list(cl) == ["inv(A=nand2(A=*,B=*))"], list(cl)
    inst = cl["inv(A=nand2(A=*,B=*))"]["instances"][0]
    assert sorted(inst["members"]) == ["u3", "u4"] and inst["leaves"] == [["u3", "A"], ["u3", "B"]]

import cellgen

def test_logic():
    # f = a&b (and2): minimal SOP one cube; realized as nand stage + output inverter
    r = cellgen.realize(2, 0b1000)
    assert r["feasible"] and r["invert_out"] and r["tx"] == 6, r
    # f = !(a&b | c) (a21oi): single stage, no inverters, 6 transistors
    tt = sum(1 << m for m in range(8) if not (((m & 1) and (m >> 1 & 1)) or (m >> 2 & 1)))
    r = cellgen.realize(3, tt)
    assert r["feasible"] and not r["invert_out"] and r["neg_inputs"] == [] and r["tx"] == 6, r
    # 6-input AND needs a 6-deep stack: infeasible
    assert not cellgen.realize(6, 1 << 63)["feasible"]
    # constant function: infeasible, no crash
    assert not cellgen.realize(2, 0b1111)["feasible"]
    # sop covers exactly
    for n, tt in [(3, 0b10010110), (4, 0xBEEF)]:
        cubes = cellgen.sop(n, tt)
        got = sum(1 << m for m in range(1 << n) if any((m & mk) == v for mk, v in cubes))
        assert got == tt

def test_oracle_sanity():
    """nand2 -> inv equals and2: fused and original must be within 25%."""
    lib = netlist.load_liberty(os.environ["LIB"])
    tree = {"cell": "sky130_fd_sc_hd__inv_1", "out": "Y",
            "ins": {"A": {"cell": "sky130_fd_sc_hd__nand2_1", "out": "Y", "ins": {"A": None, "B": None}}}}
    res = cellgen.measure({"k": {"tree": tree, "n_in": 2, "late": 0, "load": 0.005}}, lib)
    r = res["k"]["ratio"]
    assert all(0.75 < x < 1.25 for x in r) and None not in res["k"]["fused_ps"], res

import score

def test_score():
    hits = [{"slack": -0.10, "hits": [
                {"key": "A", "members": ["u1", "u2"], "entry": 0, "delay": 0.2},
                {"key": "B", "members": ["u2", "u3"], "entry": 0, "delay": 0.3},   # overlaps A on u2
                {"key": "C", "members": ["u9", "u8"], "entry": 0, "delay": 0.1}]},
            {"slack": -0.05, "hits": [{"key": "C", "members": ["u7", "u6"], "entry": 0, "delay": 0.1}]}]
    orc = {"A": {"feasible": True, "ratio": [0.8], "tx_orig": 10, "tx_fused": 6},
           "B": {"feasible": True, "ratio": [0.5], "tx_orig": 10, "tx_fused": 6},
           "C": {"feasible": True, "ratio": [1.3], "tx_orig": 10, "tx_fused": 6}}
    cl = {"A": {"count": 1, "crit": 1}, "B": {"count": 1, "crit": 1}, "C": {"count": 2, "crit": 1}}
    s = score.score(["A", "B"], hits, orc, cl)
    # only B (saving 0.15) applies on path 0 -> slack -0.10+0.15=+0.05 ; WNS = path1 = -0.05
    assert abs(s["dwns"] - 50.0) < 1e-6, s
    s = score.score(["C"], hits, orc, cl)          # slower fused cell: clipped, no gain
    assert s["dwns"] == 0 and s["dtns"] == 0
    best, label = score.best_subset(["A", "B", "C"], 1, hits, orc, cl)
    assert best == ["B"] and label == "exhaustive", (best, label)

import selectors_

def test_parse_llm():
    pl = ["a(A=*)", "b(A=*)", "c(A=*)"]
    txt = 'blah\n```json\n{"picks": [{"key": "b(A=*)", "why": "x"}, {"key": "zzz", "why": "y"}]}\n```'
    assert selectors_.parse_llm(txt, pl, 2) == ["b(A=*)"]
    assert selectors_.parse_llm("no json here", pl, 2) == []
    assert selectors_.parse_llm('```json\n{"picks": "oops"}\n```', pl, 2) == []

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
