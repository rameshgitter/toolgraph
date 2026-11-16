# ToolGraph + a HydraDB node in one container. Exposes the web UI on $PORT (default 8080).
# Stage 1 builds the open-source HydraDB engine from source (github.com/hydra-db/hydradb).
# Expect ~10-15 minutes the first time (Rust release build).

FROM ubuntu:24.04 AS build
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential clang libclang-dev cmake pkg-config libgraphblas-dev git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# rustup reads the repo's rust-toolchain.toml (pins 1.99.0) and installs it on first cargo call
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain none
ENV PATH=/root/.cargo/bin:$PATH
# LAGraph is not packaged for Ubuntu. Same cmake flags as hydradb/scripts/install_lagraph.sh,
# fetched with git because release-tarball URLs are sometimes blocked.
RUN git clone --depth 1 --branch v1.0.2 https://github.com/GraphBLAS/LAGraph.git /src/LAGraph \
 && cd /src/LAGraph \
 && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
          -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_C_FLAGS=-fPIC \
 && cmake --build build --target lagraph_static -j"$(nproc)" \
 && install -D build/liblagraph.a /opt/lagraph/lib/liblagraph.a \
 && install -D include/LAGraph.h /opt/lagraph/include/LAGraph.h
RUN git clone --depth 1 https://github.com/hydra-db/hydradb.git /src/hydradb
WORKDIR /src/hydradb
ENV LAGRAPH_LIB_DIR=/opt/lagraph/lib RUST_MIN_STACK=33554432
RUN cargo build --release --locked --features server-runtime --bin graph-node

FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 libgraphblas7 libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/hydradb/target/release/graph-node /usr/local/bin/graph-node
WORKDIR /app
COPY toolgraph.py experiment.py server.py run_node.sh start.sh ./
COPY static ./static
COPY results/curve.json results/curve.png ./results/
RUN chmod +x run_node.sh start.sh
ENV PORT=8080 GRAPH_NODE_BIN=/usr/local/bin/graph-node DATA_DIR=/tmp/hydradb-data RUST_MIN_STACK=33554432
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fs http://127.0.0.1:${PORT}/healthz || exit 1
CMD ["./start.sh"]
