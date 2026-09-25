#!/bin/sh
# usage: ./run.sh [design ...]   (default: all designs in designs.txt)
cd "$(dirname "$0")"
mkdir -p results
PDK=$(ls -d ~/.ciel/ciel/sky130/versions/*/sky130A | head -1)
LIB=$PDK/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
export PDK LIB
N=${N:-200}
grep -v '^#' designs.txt | while read name repo commit top files; do
  [ -z "$name" ] && continue
  if [ $# -gt 0 ] && ! echo " $* " | grep -q " $name "; then continue; fi
  echo "== $name"
  src=src/$(basename "$repo"); out=build/$name; mkdir -p "$out"
  rv=""; for f in $(echo "$files" | tr , ' '); do rv="$rv $src/$f"; done
  if [ "$top" = "-" ]; then topopt="-auto-top"; else topopt="-top $top"; fi
  yosys -q -l "$out/yosys.log" -p "read_liberty -lib $LIB; read_verilog $rv; hierarchy -check $topopt; synth -flatten; dfflibmap -liberty $LIB; abc -liberty $LIB; setundef -zero; hilomap -singleton -hicell sky130_fd_sc_hd__conb_1 HI -locell sky130_fd_sc_hd__conb_1 LO; opt_clean -purge; rename -enumerate -pattern u_% t:*; write_json $out/net.json; write_verilog -noattr -noexpr $out/net.v" </dev/null || { echo "$name: synth failed" >> results/failed.txt; continue; }
  tn=$(python3 -c "import json;d=json.load(open('$out/net.json'));print(next(k for k,m in d['modules'].items() if m['attributes'].get('top')))")
  sed -e "s|@LIB@|$LIB|;s|@NET@|$out/net.v|;s|@TOP@|$tn|;s|@P@|100|;s|@N@|1|" sta.tcl > "$out/sta1.tcl"
  ws=$(sta -no_init -no_splash -exit "$out/sta1.tcl" </dev/null | awk '/worst slack/{print $NF}')
  p=$(python3 -c "print(round(0.8*(100-($ws)),4))")
  sed -e "s|@LIB@|$LIB|;s|@NET@|$out/net.v|;s|@TOP@|$tn|;s|@P@|$p|;s|@N@|$N|" sta.tcl > "$out/sta2.tcl"
  sta -no_init -no_splash -exit "$out/sta2.tcl" </dev/null > "$out/paths.rpt"
  echo "{\"top\": \"$tn\", \"period\": $p}" > "$out/meta.json"
done
