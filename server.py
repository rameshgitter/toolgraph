#!/usr/bin/env python3
"""Web demo for ToolGraph. Standard library only. HydraDB is the only data store.

  GET  /                 the page
  GET  /curve.png        precomputed 5-seed learning curve (from experiment.py)
  GET  /api/state        catalog stats, tool + skill names, live training history
  POST /api/pull         {task}                         -> context pulled from the graph
  POST /api/feedback     {task, used[], skill?}         -> graph rewritten, new pull returned
  POST /api/train        {rounds}                       -> simulate agent usage, re-score held-out set
  POST /api/reset        {}                             -> wipe the learned state, reseed
"""
import argparse, json, os, random, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import toolgraph as tg
import experiment as ex

HERE = os.path.dirname(os.path.abspath(__file__))
WRITE_LOCK = threading.Lock()
STATE = {"history": [], "rnd": random.Random(7), "trained": 0}
TEST = ex.make_test_set()


def wait_for_hydradb(timeout=120):
    t0 = time.time()
    while True:
        try:
            tg.q("MATCH (t:Tool) RETURN count(*) AS n")
            return
        except Exception as e:
            if time.time() - t0 > timeout:
                raise SystemExit(f"HydraDB not reachable at {tg.HOST}: {e}")
            time.sleep(1)


def snapshot():
    m = ex.evaluate("graph", TEST)
    m["round"] = len(STATE["history"])
    m["trained_tasks"] = STATE["trained"]
    STATE["history"].append(m)
    return m


def init_graph(force=False):
    wait_for_hydradb()
    if force or tg.catalog_stats()["tools"] == 0:
        tg.seed(1)
    STATE["history"], STATE["trained"], STATE["rnd"] = [], 0, random.Random(7)
    snapshot()


def public(ctx):
    kinds = {"action": 0, "dependency": 1, "resolver": 2}
    tools = sorted(({"name": n, "tokens": t, "kind": k} for (n, t, k) in ctx["tools"].values()),
                   key=lambda x: (kinds[x["kind"]], x["name"]))
    pre = tg.catalog_stats()["preload_tokens"]
    return {"task": None, "skills": sorted(ctx["skills"].values()), "tools": tools, "path": ctx["path"],
            "entities_hit": ctx["entities_hit"], "entities_missed": ctx["entities_missed"],
            "pruned_resolvers": ctx["pruned_resolvers"], "tool_tokens": ctx["tool_tokens"],
            "skill_tokens": ctx["skill_tokens"], "tokens": ctx["tokens"], "preload_tokens": pre,
            "reduction": round(1 - ctx["tokens"] / pre, 4) if pre else 0, "graph_queries": ctx["graph_queries"]}


class H(BaseHTTPRequestHandler):
    server_version = "ToolGraph/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/index.html"):
                return self._send(200, open(os.path.join(HERE, "static", "index.html"), "rb").read(), "text/html; charset=utf-8")
            if p == "/curve.png":
                return self._send(200, open(os.path.join(HERE, "results", "curve.png"), "rb").read(), "image/png")
            if p == "/api/curve":
                return self._send(200, open(os.path.join(HERE, "results", "curve.json"), "rb").read())
            if p == "/api/state":
                return self._send(200, {"catalog": tg.catalog_stats(), "tools": sorted(tg.tool_ids()),
                                        "skills": sorted(n for n, _ in tg.skill_tools().values()),
                                        "history": STATE["history"], "n_test": len(TEST)})
            if p == "/healthz":
                return self._send(200, {"ok": True})
        except FileNotFoundError:
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            p = self.path
            if p == "/api/pull":
                task = str(body.get("task", ""))[:300]
                out = public(tg.pull_context(task))
                out["task"] = task
                return self._send(200, out)
            if p == "/api/feedback":
                task = str(body.get("task", ""))[:300]
                used = [u for u in body.get("used", []) if u in tg.tool_ids()]
                skill = body.get("skill") or None
                with WRITE_LOCK:
                    before = tg.pull_context(task)
                    log = tg.learn(task, before, used, skill=skill)
                    after = tg.pull_context(task)
                return self._send(200, {"log": log, "before": public(before), "after": public(after)})
            if p == "/api/train":
                rounds = max(1, min(int(body.get("rounds", 1)), 5))
                with WRITE_LOCK:
                    for _ in range(rounds):
                        for _ in range(15):
                            t = ex.train_task(STATE["rnd"])
                            skill = ex.INTENTS[t["intent"]][0]
                            ctx = tg.pull_context(t["task"])
                            tg.learn(t["task"], ctx, ex.needed(t["intent"], bool(ctx["entities_missed"])), skill=skill)
                            STATE["trained"] += 1
                        snapshot()
                return self._send(200, {"history": STATE["history"]})
            if p == "/api/reset":
                with WRITE_LOCK:
                    init_graph(force=True)
                return self._send(200, {"history": STATE["history"]})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    init_graph(force=os.environ.get("KEEP_GRAPH") != "1")   # fresh, unlearned graph on every boot
    print(f"ToolGraph on http://{a.host}:{a.port}  (HydraDB {tg.HOST})", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
