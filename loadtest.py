#!/usr/bin/env python3
"""Concurrent context pulls against HydraDB at two catalog sizes.

    python3 loadtest.py [--threads 16] [--per-thread 150]

Each pull is ~10 Cypher queries (keyword -> skill -> tool, NEEDS*1..3 traversal, entity lookups).
The graph is reseeded (and left unlearned) for each catalog size.
"""
import argparse, json, random, threading, time
import toolgraph as tg
import experiment as ex


def run(threads, per_thread):
    prompts = [t["task"] for t in ex.make_test_set()]
    lat, errs, lk = [], [0], threading.Lock()

    def work(seed):
        rnd, mine = random.Random(seed), []
        for _ in range(per_thread):
            t0 = time.perf_counter()
            try:
                tg.pull_context(rnd.choice(prompts))
            except Exception:
                with lk:
                    errs[0] += 1
                continue
            mine.append((time.perf_counter() - t0) * 1000)
        with lk:
            lat.extend(mine)

    n0, t0 = tg.STATS["queries"], time.time()
    ts = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    dt = time.time() - t0
    lat.sort()
    pct = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))]
    cs = tg.catalog_stats()
    return {"catalog_tools": cs["tools"], "preload_tokens": cs["preload_tokens"], "threads": threads,
            "pulls": len(lat), "errors": errs[0], "pulls_per_s": round(len(lat) / dt, 1),
            "queries_per_s": round((tg.STATS["queries"] - n0) / dt), "p50_ms": round(pct(.5), 1),
            "p95_ms": round(pct(.95), 1), "p99_ms": round(pct(.99), 1)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--per-thread", type=int, default=150)
    a = ap.parse_args()
    out = []
    for scale in (1, 10):
        tg.seed(scale)
        r = run(a.threads, a.per_thread)
        r["pull_tokens_example"] = tg.pull_context("Email Acme a quick follow up about the renewal")["tokens"]
        print(json.dumps(r), flush=True)
        out.append(r)
    tg.seed(1)
    json.dump(out, open("results/load.json", "w"), indent=1)
