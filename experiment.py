#!/usr/bin/env python3
"""Learning-curve experiment for ToolGraph.

Question: does the graph get better at pulling exactly the context a task needs as the agent
uses it, and does it beat (a) the same graph frozen and (b) a plain keyword->tool table?

Setup
  * 13 task intents, each with 6 hand-written phrasings. Phrasings 0,2,4 are TRAIN, 1,3,5 are TEST.
    The held-out test phrasings reuse vocabulary from training but are never seen verbatim.
  * Ground truth per intent: the tools a correct agent must have (+ resolver tools while the
    named entity is still unresolved).
  * Each round the system serves 15 training tasks, then gets feedback (which tools were needed),
    then is scored on the fixed held-out test set. Learning never touches test tasks.

Systems
  graph_learning   HydraDB graph + learning loop (this project)
  graph_static     the same HydraDB graph, never updated          (baseline: iteration 0)
  flat_static      plain keyword -> tool table, no deps, no learning (baseline: "plain store")
  flat_learning    plain table + the same feedback rule              (baseline: fair learner)

Metrics (per held-out task, then averaged)
  success    every needed tool was in the pulled context (agent can finish without extra lookups)
  precision  share of pulled tools that were needed (what the context window pays for)
  f1         harmonic mean of recall and precision
  tool_tokens  tokens of tool definitions loaded
  round_trips  1 + 2 per unresolved entity (model turns before acting)
"""
import argparse, json, random, statistics, sys, time
import toolgraph as tg

COMPANIES = ["Acme", "Initech", "Globex", "Hooli", "Umbrella", "Stark", "Wayne", "Wonka"]
SERVICES = ["Checkout", "Payments", "Search", "Gateway"]
POOL = {"co": COMPANIES, "svc": SERVICES, None: [None]}
CRM_RES = ["salesforce.find_contact", "salesforce.search_accounts"]

# name: (skill, entity kind, needed tools, resolver tools, train templates, test templates)
INTENTS = {
    "outreach_email": ("customer-outreach", "co", ["gmail.send_email"], CRM_RES,
        ["Email {E} a quick follow up about the renewal", "Drop {E} a note by mail about the new pricing", "Reach out to {E} and ask about next steps"],
        ["Send {E} an intro about our offer", "Reach out to {E} and ask for feedback on the pricing", "Write {E} a note by mail about the proposal"]),
    "outreach_log": ("customer-outreach", "co", ["gmail.send_email", "salesforce.log_activity"], CRM_RES,
        ["Email {E} an intro and log the activity", "Send mail to {E} then record the activity in the account", "Reply to {E} and keep a record of it"],
        ["Follow up with {E} by mail and record the activity", "Reply to {E} then log the activity", "Write to {E} and keep a record of the exchange"]),
    "crm_log": ("customer-outreach", "co", ["salesforce.log_activity"], CRM_RES,
        ["Record that we spoke with {E} today", "Add an update to the {E} account history", "Note down the conversation with {E} in the account"],
        ["Keep a record of my call with {E}", "Update the {E} account with what we discussed", "Add a summary of the conversation with {E} to the history"]),
    "refund": ("refund-handling", "co", ["stripe.refund_charge", "stripe.list_charges"], ["stripe.find_customer"],
        ["Refund the duplicate charge for {E}", "Return the money to {E} for the double payment", "Give {E} their cash back after the overcharge"],
        ["Process the chargeback for {E}", "Send the money back to {E} after the duplicate payment", "Reverse the overcharge on the {E} account"]),
    "refund_ticket": ("refund-handling", "co", ["stripe.refund_charge", "stripe.list_charges", "jira.create_ticket"], ["stripe.find_customer", "jira.get_project"],
        ["Refund {E} and open a ticket for it", "Return the money to {E} and file a ticket", "Credit {E} back and log the issue for follow up"],
        ["Process the chargeback for {E} and open a ticket", "Give {E} their money back and file a ticket", "Reverse the double payment for {E} and raise a ticket"]),
    "meeting": ("meeting-scheduling", "co", ["gcal.create_event", "gcal.find_free_time"], ["slack.get_user"],
        ["Schedule a meeting with {E} next week", "Find a slot to catch up with {E} on thursday", "Book some time with {E} tomorrow"],
        ["Put a calendar invite on the books for {E}", "Set up a time to catch up with {E} next week", "Find some time with {E} on thursday"]),
    "incident": ("incident-triage", "svc", ["datadog.query_metrics", "datadog.list_monitors", "pagerduty.get_incident"], ["pagerduty.list_services"],
        ["The {E} service is down, triage the incident", "Why is {E} so slow right now, check the metrics", "Users say {E} is timing out in prod"],
        ["Latency spiked on {E}, check the pager", "Something is wrong with {E}, look at the metrics", "Users report that {E} is slow right now"]),
    "postmortem": ("docs-writeup", None, ["notion.create_page", "github.get_file"], [],
        ["Write the postmortem notes for last night's outage", "Capture what happened in a wiki page for the team", "Draft a writeup of the failure and the fix"],
        ["Document the outage in the wiki", "Write up what broke and how we fixed it", "Capture the failure notes for the team"]),
    "release": ("release-management", None, ["github.merge_pull_request", "github.get_pull_request", "github.list_workflows"], [],
        ["Ship the release", "Get the approved changes into main", "Roll out the latest build to production"],
        ["Merge the approved change", "Land the reviewed changes into main", "Roll out the new build"]),
    "release_announce": ("release-management", None, ["github.merge_pull_request", "github.get_pull_request", "github.list_workflows", "slack.post_message"], [],
        ["Ship the release and tell the team", "Get the changes into main and let everyone know", "Roll out the latest build and announce it in chat"],
        ["Deploy it and let everyone know", "Merge the approved change and tell the team in chat", "Land the reviewed changes and announce it"]),
    "data": ("data-pull", None, ["postgres.run_query"], [],
        ["Pull the weekly revenue numbers", "How many users joined last month", "Count the orders from yesterday"],
        ["Run a sql query on signups", "How many orders came in last month", "Count the users who joined yesterday"]),
    "data_report": ("data-pull", None, ["postgres.run_query", "notion.create_page"], [],
        ["Pull the revenue numbers into a report", "Count the orders from yesterday and write a page about it", "How many users joined last month, put it in a summary page"],
        ["Run a query and put the results in a report", "Count the signups from yesterday and write up a page", "How many users joined, summarize in a page"]),
    "storage": ("storage-ops", None, ["s3.put_object"], [],
        ["Upload the quarterly archive to the bucket", "Store the backup files somewhere safe", "Save the exports in cloud storage"],
        ["Archive the old logs", "Store the exports somewhere safe", "Save the backup files in cloud storage"]),
}


def render(tpl, ent):
    return tpl.replace("{E}", ent) if ent else tpl


def make_test_set():
    rnd = random.Random(1234)
    out = []
    for name, (skill, kind, need, res, _, test) in INTENTS.items():
        for tpl in test:
            ents = rnd.sample(POOL[kind], 2) if kind else [None]
            for e in ents:
                out.append({"intent": name, "task": render(tpl, e)})
    return out


def train_task(rnd):
    name = rnd.choice(list(INTENTS))
    skill, kind, need, res, train, _ = INTENTS[name]
    return {"intent": name, "task": render(rnd.choice(train), rnd.choice(POOL[kind]))}


def needed(intent, unresolved):
    _, _, need, res, _, _ = INTENTS[intent]
    return set(need) | (set(res) if unresolved else set())


def score(got, need):
    hit = len(got & need)
    rec = hit / len(need)
    pre = hit / len(got) if got else 0.0
    f1 = 2 * rec * pre / (rec + pre) if rec + pre else 0.0
    return {"success": float(rec == 1.0), "recall": rec, "precision": pre, "f1": f1}


# ------------------------------------------------------------- flat-table baseline
class Flat:
    """Plain keyword -> tool table. No skills, no NEEDS edges. Optional learning with the same rule."""

    def __init__(self, learning):
        self.learning, self.resolved = learning, set()
        self.m = {}
        for ws, ts in tg.SKILLS.values():
            for w in ws:
                for t in ts:
                    self.m.setdefault(w, {})[t] = [tg.PRIOR_HIT, 0]
        cat = tg.build_catalog(1)
        self.tok = {n: tg.toks(d) for n, d in cat.items()}

    def pull(self, task):
        tools = {t for w in tg.words(task) for t, (h, m) in self.m.get(w, {}).items() if tg.weight(h, m) >= tg.THRESH}
        missed = [e for e in tg.entities(task) if e not in self.resolved]
        return {"tools": tools, "missed": missed, "tool_tokens": sum(self.tok[t] for t in tools)}

    def learn(self, task, ctx, used):
        if not self.learning:
            return
        used = set(used)
        for w in tg.words(task):
            for t, hm in self.m.get(w, {}).items():
                if t in ctx["tools"] and tg.weight(*hm) >= tg.THRESH:
                    hm[0 if t in used else 1] += 1
        for t in used - ctx["tools"]:
            for w in tg.words(task):
                if w in {e.lower() for e in tg.entities(task)}:
                    continue
                hm = self.m.setdefault(w, {}).get(t)
                if hm is None:
                    self.m[w][t] = [1, 2]
                elif tg.weight(*hm) < tg.THRESH:
                    hm[0] += 1
        self.resolved.update(ctx["missed"])


# --------------------------------------------------------------------- evaluation
def evaluate(system, test):
    rows = []
    for t in test:
        if system == "graph":
            ctx = tg.pull_context(t["task"])
            got, missed, tok = set(ctx["tool_names"]), ctx["entities_missed"], ctx["tool_tokens"]
        else:
            ctx = system.pull(t["task"])
            got, missed, tok = ctx["tools"], ctx["missed"], ctx["tool_tokens"]
        need = needed(t["intent"], bool(missed))
        s = score(got, need)
        s.update({"tool_tokens": tok, "round_trips": 1 + 2 * len(missed)})
        rows.append(s)
    return {k: statistics.mean(r[k] for r in rows) for k in rows[0]}


def run_seed(seed, rounds, per_round, test, verbose=False):
    tg.seed(1)
    rnd = random.Random(seed)
    flat_s, flat_l = Flat(False), Flat(True)
    series = {k: [] for k in ("graph_learning", "flat_static", "flat_learning")}

    def snap():
        series["graph_learning"].append(evaluate("graph", test))
        series["flat_static"].append(evaluate(flat_s, test))
        series["flat_learning"].append(evaluate(flat_l, test))

    snap()
    for r in range(rounds):
        for _ in range(per_round):
            t = train_task(rnd)
            skill, kind, need, res, _, _ = INTENTS[t["intent"]]
            ctx = tg.pull_context(t["task"])
            used = needed(t["intent"], bool(ctx["entities_missed"]))
            tg.learn(t["task"], ctx, used, skill=skill)
            fctx = flat_l.pull(t["task"])
            flat_l.learn(t["task"], fctx, needed(t["intent"], bool(fctx["missed"])))
        snap()
        if verbose:
            g = series["graph_learning"][-1]
            print(f"  seed {seed} round {r+1:2d}  success {g['success']:.2f}  f1 {g['f1']:.2f}  tokens {g['tool_tokens']:.0f}", flush=True)
    return series


def aggregate(all_series):
    out = {}
    for sysname in all_series[0]:
        out[sysname] = {}
        for metric in all_series[0][sysname][0]:
            per_it = list(zip(*[[pt[metric] for pt in s[sysname]] for s in all_series]))
            out[sysname][metric] = {"mean": [statistics.mean(v) for v in per_it],
                                    "min": [min(v) for v in per_it], "max": [max(v) for v in per_it]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--per-round", type=int, default=15)
    ap.add_argument("--out", default="results/curve.json")
    a = ap.parse_args()
    test = make_test_set()
    print(f"{len(test)} held-out test tasks, {a.seeds} seeds x {a.rounds} rounds x {a.per_round} training tasks", flush=True)
    t0 = time.time()
    all_series = []
    for s in range(a.seeds):
        all_series.append(run_seed(s, a.rounds, a.per_round, test, verbose=(s == 0)))
        print(f"seed {s} done  ({time.time()-t0:.0f}s)", flush=True)
    agg = aggregate(all_series)
    import os
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump({"config": vars(a), "n_test": len(test), "per_seed": all_series, "agg": agg,
               "catalog": tg.catalog_stats()}, open(a.out, "w"), indent=1)
    g, fs, fl = agg["graph_learning"], agg["flat_static"], agg["flat_learning"]
    print("\n                      iter0   final   (mean of %d seeds, range at final)" % a.seeds)
    for m in ("success", "recall", "precision", "f1", "tool_tokens", "round_trips"):
        for name, ag in (("graph_learning", g), ("flat_learning", fl), ("flat_static", fs)):
            v = ag[m]
            print(f"  {m:12} {name:15} {v['mean'][0]:7.2f} {v['mean'][-1]:7.2f}   [{v['min'][-1]:.2f}-{v['max'][-1]:.2f}]")
        print()


if __name__ == "__main__":
    main()
