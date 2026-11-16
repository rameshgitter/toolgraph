#!/usr/bin/env bash
# Container entrypoint: start the HydraDB node, then the web server (which seeds a fresh graph).
set -euo pipefail
cd "$(dirname "$0")"
export DATA_DIR="${DATA_DIR:-/tmp/hydradb-data}"
rm -rf "$DATA_DIR"                         # the demo always boots from an unlearned graph
LOG=/dev/null; [ "${DEBUG:-0}" = "1" ] && LOG="${DATA_DIR}.node.log"
./run_node.sh > "$LOG" 2>&1 &
NODE=$!
trap 'kill $NODE 2>/dev/null || true' EXIT
python3 server.py --port "${PORT:-8080}"   # waits up to 120s for HydraDB, then seeds and serves
