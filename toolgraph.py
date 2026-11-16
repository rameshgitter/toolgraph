#!/usr/bin/env python3
"""ToolGraph: a self-improving context layer for agents, built on HydraDB.

HydraDB (the open-source graph engine) is the only store. It holds:

  (:Kw)-[:TRIGGERS {hit,miss}]->(:Skill)-[:USES {hit,miss}]->(:Tool)
  (:Tool)-[:NEEDS]->(:Tool)            tool dependencies (multi-hop)
  (:Entity)-[:RESOLVED_BY]->(:Tool)    entities the agent already resolved

pull_context(task) walks that graph and returns only what the task needs.
learn(task, ctx, used) takes the agent's feedback (which tools it really used)
and rewrites the graph:

  * edge reweighting       hit/miss counters on TRIGGERS and USES edges
  * gap-driven ingestion   new keyword -> skill edges learned from misses
  * entity cache           resolved entities written back, resolver tools pruned

Standard library only. Talks to HydraDB over its HTTP query API (Cypher).
"""
import json, math, os, threading, zlib
import http.client

HOST = os.environ.get("HYDRA_HOST", "127.0.0.1:8443")
TOKEN = os.environ.get("HYDRA_TOKEN", "local-development-token-32-bytes")
GRAPH = os.environ.get("HYDRA_GRAPH", "default")
CELL = os.environ.get("HYDRA_CELL", "cell-0")
THRESH = 0.5          # an edge is active when hit / (hit + miss) >= THRESH
PRIOR_HIT = 2         # seeded edges start at hit=2, miss=0  (weight 1.0)

# ----------------------------------------------------------------- HydraDB client
_local = threading.local()
STATS = {"queries": 0}
_lock = threading.Lock()


def _conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = _local.c = http.client.HTTPConnection(HOST, timeout=60)
    return c


def _cell(v):
    t = v.get("type")
    if t == "null":
        return None
    if t == "list":
        return [_cell(x) for x in v["value"]]
    return v.get("value")


def q(query, **params):
    """Run one Cypher statement against HydraDB, return rows as dicts."""
    body = json.dumps({"cell_id": CELL, "query": query, "parameters": params})
    hdr = {"Authorization": f"Bearer {TOKEN}", "X-Graph-Namespace": "default",
           "Content-Type": "application/json"}
    for attempt in (0, 1):
        try:
            c = _conn()
            c.request("POST", f"/v1/graphs/{GRAPH}/query", body, hdr)
            raw = c.getresponse().read()
            break
        except (http.client.HTTPException, OSError):
            _local.c = None
            if attempt:
                raise
    with _lock:
        STATS["queries"] += 1
    p = json.loads(raw)
    if "rows" not in p:
        raise RuntimeError(f"HydraDB error: {raw[:300]!r}")
    cols = p["columns"]
    return [dict(zip(cols, (_cell(x) for x in row))) for row in p["rows"]]


# ------------------------------------------------------------------- id scheme
SKILL0, TOOL0, KW0, ENT0 = 1_000_000, 2_000_000, 10_000_000_000, 20_000_000_000


def h(s):
    return zlib.crc32(s.lower().encode())


def kw_id(word):
    return KW0 + h(word)


def ent_id(name):
    return ENT0 + h(name)


def toks(obj):
    """Approximate token count (chars / 4) of a JSON-serialisable definition."""
    return math.ceil(len(json.dumps(obj, separators=(",", ":"))) / 4)


def weight(hit, miss):
    return hit / (hit + miss) if (hit + miss) else 0.0


# --------------------------------------------------------------------- catalog
SERVERS = {
    "github": "search_issues:Search issues and pull requests across repos|get_pull_request:Get a pull request with diff stats and CI status|list_commits:List recent commits on a branch|create_issue:Open a new issue|merge_pull_request:Merge a pull request|list_workflows:List CI workflow runs and their status|get_file:Read a file from a repository",
    "slack": "post_message:Post a message to a channel|search_messages:Search workspace messages|list_channels:List channels|get_user:Look up a user by name or email|create_channel:Create a channel",
    "gmail": "send_email:Send an email|create_draft:Create an email draft|search_threads:Search mail threads|read_thread:Read a mail thread",
    "gcal": "create_event:Create a calendar event with invitees|find_free_time:Find mutual free slots|list_events:List upcoming events",
    "jira": "create_ticket:Create a ticket|get_project:Look up a project and its issue types|transition_issue:Move an issue to a new status|search_issues:JQL search",
    "notion": "create_page:Create a page|search:Search the workspace|update_page:Edit a page",
    "stripe": "refund_charge:Refund a charge|find_customer:Find a customer by email or name|list_charges:List charges for a customer|get_invoice:Fetch an invoice",
    "salesforce": "find_contact:Find a contact by name|search_accounts:Search accounts by name|log_activity:Log an activity on a contact|update_opportunity:Update an opportunity",
    "datadog": "query_metrics:Query a metric time series|list_monitors:List monitors and their state|get_dashboard:Fetch a dashboard",
    "pagerduty": "get_incident:Get an incident and its timeline|list_services:List services|acknowledge_incident:Acknowledge an incident",
    "postgres": "run_query:Run a read-only SQL query|list_tables:List tables and columns",
    "s3": "put_object:Upload an object|list_objects:List objects in a bucket|get_object:Download an object",
}
RESOLVERS = {"salesforce.find_contact", "salesforce.search_accounts", "stripe.find_customer",
             "jira.get_project", "slack.get_user", "pagerduty.list_services"}
NEEDS = {  # tool -> tools it needs first (the multi-hop edges)
    "gmail.send_email": ["salesforce.find_contact"],
    "salesforce.find_contact": ["salesforce.search_accounts"],
    "salesforce.log_activity": ["salesforce.find_contact"],
    "stripe.refund_charge": ["stripe.list_charges"],
    "stripe.list_charges": ["stripe.find_customer"],
    "jira.create_ticket": ["jira.get_project"],
    "pagerduty.get_incident": ["pagerduty.list_services"],
    "github.merge_pull_request": ["github.get_pull_request", "github.list_workflows"],
    "gcal.create_event": ["gcal.find_free_time", "slack.get_user"],
    "datadog.query_metrics": ["datadog.list_monitors"],
}
SKILLS = {  # name -> (seed trigger words, tools)
    "incident-triage": (["incident", "outage", "pager", "alert", "latency", "down"],
                        ["datadog.query_metrics", "pagerduty.get_incident", "slack.post_message", "github.list_commits"]),
    "customer-outreach": (["email", "outreach", "follow", "intro", "reply"],
                          ["gmail.send_email", "gmail.create_draft", "salesforce.log_activity"]),
    "refund-handling": (["refund", "chargeback", "billing", "charge"],
                        ["stripe.refund_charge", "jira.create_ticket", "slack.post_message"]),
    "release-management": (["release", "deploy", "merge", "ship"],
                           ["github.merge_pull_request", "slack.post_message", "jira.transition_issue"]),
    "meeting-scheduling": (["meeting", "schedule", "calendar", "invite"],
                           ["gcal.create_event", "gmail.send_email"]),
    "data-pull": (["report", "query", "revenue", "numbers", "sql"],
                  ["postgres.run_query", "postgres.list_tables", "notion.create_page"]),
    "docs-writeup": (["notes", "writeup", "postmortem", "wiki"],
                     ["notion.create_page", "notion.search", "github.get_file"]),
    "storage-ops": (["bucket", "upload", "archive"], ["s3.put_object", "s3.list_objects"]),
}
FILLER_SERVERS, FILLER_TOOLS = 18, 12  # per scale unit: enterprise "long tail"


def tool_def(server, name, desc):
    return {"name": f"{server}.{name}", "description": desc + ". Use when the task requires this capability; returns structured JSON.",
            "input_schema": {"type": "object", "required": ["target"], "properties": {
                "target": {"type": "string", "description": "Primary identifier or search text"},
                "limit": {"type": "integer", "description": "Maximum results to return"},
                "options": {"type": "object", "description": "Optional provider-specific filters"}}}}


def build_catalog(scale=1):
    tools = {}
    for s, spec in SERVERS.items():
        for item in spec.split("|"):
            n, d = item.split(":")
            tools[f"{s}.{n}"] = tool_def(s, n, d)
    for i in range(FILLER_SERVERS * scale):
        for j in range(FILLER_TOOLS):
            tools[f"vendor{i:03d}.op_{j:02d}"] = tool_def(f"vendor{i:03d}", f"op_{j:02d}", f"Vendor {i} operation {j}")
    return tools


def chunks(xs, n=500):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ---------------------------------------------------------------- seed / reset
_cache = {}


def wipe():
    for label in ("Tool", "Skill", "Kw", "Entity"):
        q(f"MATCH (n:{label}) DETACH DELETE n")
    _cache.clear()


def seed(scale=1, fresh=True):
    """Load the catalog into HydraDB. fresh=True wipes everything first."""
    if fresh:
        wipe()
    tools = build_catalog(scale)
    real = sorted(n for n in tools if not n.startswith("vendor"))
    fill = sorted(n for n in tools if n.startswith("vendor"))
    tid = {n: TOOL0 + i for i, n in enumerate(real + fill)}
    for part in chunks([{"id": tid[n], "name": n, "tokens": toks(tools[n]), "server": n.split(".")[0],
                          "resolver": n in RESOLVERS} for n in real + fill]):
        q("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Tool, n.name = row.name, n.tokens = row.tokens, n.server = row.server, n.resolver = row.resolver", rows=part)
    sk = {n: SKILL0 + i for i, n in enumerate(SKILLS)}
    q("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Skill, n.name = row.name, n.tokens = row.tokens",
      rows=[{"id": sk[n], "name": n, "tokens": 700 + 90 * len(SKILLS[n][1])} for n in SKILLS])
    kws = sorted({w for ws, _ in SKILLS.values() for w in ws})
    q("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Kw, n.word = row.word",
      rows=[{"id": kw_id(w), "word": w} for w in kws])
    # edges that carry learned weights: HydraDB needs one CREATE per edge when edges have properties
    for n, (ws, ts) in SKILLS.items():
        for w in ws:
            q("CREATE (a {id: $s})-[:TRIGGERS {hit: $h, miss: 0}]->(b {id: $d})", s=kw_id(w), d=sk[n], h=PRIOR_HIT)
        for t in ts:
            q("CREATE (a {id: $s})-[:USES {hit: $h, miss: 0}]->(b {id: $d})", s=sk[n], d=tid[t], h=PRIOR_HIT)
    q("UNWIND $rows AS row CREATE (a {id: row.src})-[:NEEDS]->(b {id: row.dst})",
      rows=[{"src": tid[a], "dst": tid[b]} for a, bs in NEEDS.items() for b in bs])
    _cache.clear()
    return {"tools": len(tools), "skills": len(SKILLS), "keywords": len(kws)}


def tool_ids():
    if "tid" not in _cache:
        _cache["tid"] = {r["name"]: r["id"] for r in q("MATCH (t:Tool) RETURN t.id AS id, t.name AS name")}
    return _cache["tid"]


def skill_tools():
    """skill id -> (name, set(tool names)); membership is static, weights are not."""
    if "st" not in _cache:
        st = {}
        for r in q("MATCH (s:Skill)-[:USES]->(t:Tool) RETURN s.id AS sid, s.name AS sname, t.name AS tname"):
            st.setdefault(r["sid"], (r["sname"], set()))[1].add(r["tname"])
        _cache["st"] = st
    return _cache["st"]


# --------------------------------------------------------------- the context pull
STOP = set("the and for with from that this then into please about have need want can you our your their his her its are was were will would could should just also them they what when where which who how".split())


def words(task):
    return sorted({w for w in "".join(c.lower() if c.isalnum() else " " for c in task).split()
                   if len(w) > 2 and w not in STOP})


def entities(task):
    """Capitalised words after the first word are treated as named entities."""
    ws = task.replace(",", " ").replace(".", " ").split()
    return sorted({w for w in ws[1:] if w[0].isupper() and w.isalpha()})


def pull_context(task):
    """Walk the graph from the task text; return only the skills/tools it needs.

    Read-only. The returned dict carries the edges it used so learn() can credit them.
    """
    n0 = STATS["queries"]
    ws = words(task)
    trig, uses, sk_info, tool_info, via = {}, {}, {}, {}, {}
    for w in ws:
        for r in q("MATCH (k:Kw {id: $k})-[t:TRIGGERS]->(s:Skill)-[u:USES]->(x:Tool) "
                   "RETURN s.id AS sid, s.name AS sname, s.tokens AS stok, t.hit AS th, t.miss AS tm, "
                   "u.hit AS uh, u.miss AS um, x.id AS tid, x.name AS tname, x.tokens AS ttok", k=kw_id(w)):
            trig[(kw_id(w), r["sid"])] = (r["th"], r["tm"], w)
            uses[(r["sid"], r["tid"])] = (r["uh"], r["um"])
            sk_info[r["sid"]] = (r["sname"], r["stok"])
            tool_info[r["tid"]] = (r["tname"], r["ttok"])
    active_skills = {}
    for (kid, sid), (th, tm, w) in trig.items():
        if weight(th, tm) >= THRESH:
            active_skills.setdefault(sid, []).append(w)
    active_tools = {}      # tid -> [skill ids]
    for (sid, tid), (uh, um) in uses.items():
        if sid in active_skills and weight(uh, um) >= THRESH:
            active_tools.setdefault(tid, []).append(sid)

    # transitive dependencies: bounded variable-length traversal in HydraDB
    deps = {}
    for tid in active_tools:
        for r in q("MATCH (a:Tool {id: $t})-[:NEEDS*1..3]->(d:Tool) "
                   "RETURN d.id AS tid, d.name AS tname, d.tokens AS ttok, d.resolver AS res", t=tid):
            deps[r["tid"]] = (r["tname"], r["ttok"], bool(r["res"]), tool_info.get(tid, ("?",))[0])
    # entities the agent already resolved
    hits, misses = {}, []
    for e in entities(task):
        rows = q("MATCH (e:Entity {id: $i}) RETURN e.value AS value", i=ent_id(e))
        if rows and rows[0]["value"]:
            hits[e] = rows[0]["value"]
        else:
            misses.append(e)

    tools, path = {}, []
    for tid, sids in active_tools.items():
        tools[tid] = (tool_info[tid][0], tool_info[tid][1], "action")
        for sid in sids:
            path.append({"from": sk_info[sid][0], "to": tool_info[tid][0], "rel": "USES"})
    pruned = []
    for tid, (name, tok, res, parent) in deps.items():
        if tid in tools:
            continue
        if res and not misses:      # entity already resolved -> resolver tool not needed
            pruned.append(name)
            continue
        tools[tid] = (name, tok, "resolver" if res else "dependency")
        path.append({"from": parent, "to": name, "rel": "NEEDS"})
    for sid, wl in active_skills.items():
        for w in wl:
            path.insert(0, {"from": w, "to": sk_info[sid][0], "rel": "TRIGGERS"})
    skill_tok = sum(sk_info[s][1] for s in active_skills)
    tool_tok = sum(v[1] for v in tools.values())
    return {"words": ws, "skills": {s: sk_info[s][0] for s in active_skills},
            "tools": {tid: v for tid, v in tools.items()},
            "tool_names": sorted(v[0] for v in tools.values()),
            "entities_hit": hits, "entities_missed": misses, "pruned_resolvers": sorted(set(pruned)),
            "tool_tokens": tool_tok, "skill_tokens": skill_tok, "tokens": tool_tok + skill_tok,
            "graph_queries": STATS["queries"] - n0, "path": path,
            "_trig": trig, "_uses": uses, "_active_skills": active_skills, "_active_tools": active_tools}


def remember_entity(name, value, resolver_tool):
    tid = tool_ids()[resolver_tool]
    q("MERGE (e:Entity {id: $i, name: $n})-[:RESOLVED_BY]->(t:Tool {id: $t})", i=ent_id(name), n=name, t=tid)
    q("MATCH (e:Entity {id: $i}) SET e.value = $v", i=ent_id(name), v=value)


# ------------------------------------------------------------------ the learning
def _set(rel, a, b, hit, miss):
    q(f"MATCH (a {{id: $a}})-[r:{rel}]->(b {{id: $b}}) SET r.hit = $h, r.miss = $m", a=a, b=b, h=hit, m=miss)


def learn(task, ctx, used, skill=None, resolver_tool="salesforce.search_accounts"):
    """Feed back which tools the agent really needed. Rewrites the graph. Returns a change log.

    skill: name of the skill the agent ended up following (optional). Without it the skill is
    inferred from tool overlap, which is skipped when two skills tie.
    """
    used = set(used)
    tid_of = tool_ids()
    pulled = {v[0] for v in ctx["tools"].values()}
    log = {"reweighted": 0, "learned_kw": 0, "recovered": 0, "entities": 0}

    # 1. edge reweighting. Credit only edges that fired.
    skill_useful = {}
    for sid in ctx["_active_skills"]:
        names = {ctx["tools"][t][0] for t, sids in ctx["_active_tools"].items() if sid in sids}
        skill_useful[sid] = bool(names & used)
    for (kid, sid), (th, tm, w) in ctx["_trig"].items():
        if sid in ctx["_active_skills"] and weight(th, tm) >= THRESH:
            _set("TRIGGERS", kid, sid, th + (1 if skill_useful[sid] else 0), tm + (0 if skill_useful[sid] else 1))
            log["reweighted"] += 1
    for (sid, tid), (uh, um) in ctx["_uses"].items():
        if tid in ctx["_active_tools"] and sid in ctx["_active_tools"][tid] and skill_useful.get(sid):
            ok = ctx["tools"][tid][0] in used
            _set("USES", sid, tid, uh + (1 if ok else 0), um + (0 if ok else 1))
            log["reweighted"] += 1

    # 2. gap-driven learning: something needed was not pulled
    missing = used - pulled
    if missing:
        st = skill_tools()
        score = sorted(((len(tools & used), sid) for sid, (_, tools) in st.items()), reverse=True)
        best = None
        if skill:
            best = next((sid for sid, (n, _) in st.items() if n == skill), None)
        elif score and score[0][0] > 0 and (len(score) == 1 or score[0][0] > score[1][0]):
            best = score[0][1]
        if best is not None:
            # 2a. recover a pruned USES edge for a needed tool
            for r in q("MATCH (s:Skill {id: $s})-[u:USES]->(t:Tool) RETURN t.name AS tname, t.id AS tid, u.hit AS h, u.miss AS m", s=best):
                if r["tname"] in missing and weight(r["h"], r["m"]) < THRESH:
                    _set("USES", best, r["tid"], r["h"] + 1, r["m"])
                    log["recovered"] += 1
            # 2b. teach the graph the words of this task, if the skill was not even triggered
            if best not in ctx["_active_skills"]:
                ent_words = {e.lower() for e in entities(task)}
                for w in words(task):
                    if w in ent_words:
                        continue
                    rows = q("MATCH (k {id: $k})-[t:TRIGGERS]->(s {id: $s}) RETURN t.hit AS h, t.miss AS m", k=kw_id(w), s=best)
                    if rows:
                        if weight(rows[0]["h"], rows[0]["m"]) < THRESH:
                            _set("TRIGGERS", kw_id(w), best, rows[0]["h"] + 1, rows[0]["m"])
                            log["learned_kw"] += 1
                    else:   # probation: becomes active after one more co-occurrence
                        q("UNWIND $rows AS row MERGE (n {id: row.id}) SET n:Kw, n.word = row.word", rows=[{"id": kw_id(w), "word": w}])
                        q("CREATE (a {id: $s})-[:TRIGGERS {hit: 1, miss: 2}]->(b {id: $d})", s=kw_id(w), d=best)
                        log["learned_kw"] += 1

    # 3. entity cache: the agent resolved these by calling a resolver tool
    for e in ctx["entities_missed"]:
        remember_entity(e, f"resolved:{h(e) % 100000}", resolver_tool)
        log["entities"] += 1
    return log


def catalog_stats():
    r = q("MATCH (t:Tool) RETURN count(*) AS n, sum(t.tokens) AS tok")[0]
    return {"tools": r["n"], "preload_tokens": r["tok"]}
