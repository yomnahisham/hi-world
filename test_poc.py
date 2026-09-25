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

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
