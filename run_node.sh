#!/usr/bin/env bash
# Start a single local HydraDB node (plaintext, local-directory object store).
# Usage: HYDRADB_DIR=/path/to/hydradb ./run_node.sh
set -euo pipefail
ROOT="${HYDRADB_DIR:-$(pwd)/hydradb}"
DATA="${DATA_DIR:-$PWD/.hydradb}"
mkdir -p "$DATA/store" "$DATA/cache"
printf '%s\n' 'local-development-token-32-bytes' > "$DATA/auth-token"

export CLOUD_PROVIDER=local LOCAL_PATH="$DATA/store"
export GRAPH_NAMESPACE=default GRAPH_ID=default GRAPH_CELL_ID=cell-0 GRAPH_CELLS=cell-0 GRAPH_NODE_ID=node-0
export GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687
export GRAPH_DATA_CACHE_DIR="$DATA/cache" GRAPH_DATA_CACHE_PART_BYTES=4194304 GRAPH_DATA_CACHE_MAX_OPEN_FILES=512
export GRAPH_AUTH_TOKEN_FILE="$DATA/auth-token" GRAPH_ALLOW_PLAINTEXT=true
export RUST_MIN_STACK=33554432   # without this the node aborts on the first query

BIN="${GRAPH_NODE_BIN:-$ROOT/target/release/graph-node}"
exec "$BIN"
