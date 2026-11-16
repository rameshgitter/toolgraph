# Submission guide (hackathon deadline: 5 PM IST, Mon Nov 16)

Order matters: **start the deploy first** (Step 2), because the Docker build takes 10-15 minutes and runs on its own while you do everything else.

## Step 1. Put the code on GitHub (5 min)

1. Create a **public** repo on github.com, e.g. `toolgraph`. Do not add a README or license there.
2. Unzip `toolgraph.zip`, then in that folder:
   ```bash
   git init && git add . && git commit -m "ToolGraph: self-improving agent context layer on HydraDB"
   git branch -M main
   git remote add origin https://github.com/<YOUR_USER>/toolgraph.git
   git push -u origin main
   ```
3. Add a LICENSE (MIT) from the GitHub "Add file" menu. The rules ask for an open-source repo.

## Step 2. Host it so judges can use it (15-30 min, mostly waiting)

The Dockerfile builds HydraDB from source and runs HydraDB + the web server in one container.
**It has not been built before (no Docker daemon in my sandbox), so start now and watch the first build.**

**Option A: Fly.io (recommended, the build runs on their servers)**
```bash
# install: https://fly.io/docs/flyctl/install/
fly auth signup            # or: fly auth login   (needs a card on file)
cd toolgraph
fly launch --no-deploy --copy-config --name <unique-app-name>
fly deploy --remote-only
fly open                   # your live link: https://<unique-app-name>.fly.dev
```
The node uses about 72 MB, so the 1 GB machine in `fly.toml` is plenty.

**Option B: any VPS with Docker** (Hetzner, DigitalOcean, EC2; 4 GB RAM is enough to compile)
```bash
git clone https://github.com/<YOUR_USER>/toolgraph.git && cd toolgraph
docker build -t toolgraph .          # 10-15 min
docker run -d --restart=always -p 80:8080 --name toolgraph toolgraph
# live link: http://<server-ip>/
```

**If the build fails:** copy the last 30 lines of the error and send them to me; the most likely places are the
Rust toolchain download or the LAGraph step. Don't sink more than 45 minutes. Fallback: run it on any Linux
machine you can compile on, follow `README.md` "Run it", and expose it with a free tunnel (`cloudflared tunnel --url http://localhost:8080`).
A tunnel link works for judging but dies when your machine sleeps, so say so in the form.

**Check the live link works** before moving on: open it, click an example chip, press *Simulate 1 round of agent use* three times
and confirm the success curve rises. Note: the graph resets each time the container restarts, and anyone can press Reset.

## Step 3. Record the Loom (3 minutes, do this once the link works)

Record against the **live link**. Rough script:

1. (0:00) "Agents drag every tool into the prompt. With 2,000 tools that's 238,000 tokens before they've done anything. ToolGraph keeps tools, skills and resolved entities in a HydraDB graph and pulls only what a task needs."
2. (0:25) Show the page. Click *Email Acme...* Point at the path: keyword -> skill -> tool -> NEEDS. "The dependency `search_accounts` came from a multi-hop traversal in HydraDB, not from the model guessing."
3. (1:00) Click *Drop Initech a note by mail...* "No skill matched. The graph hasn't learned these words." Tick `gmail.send_email`, pick skill `customer-outreach`, Send feedback twice. "New words start on probation; the second confirmation activates them."
4. (1:40) Scroll to the live curve. Reset, press *5 rounds*. "Held-out task success goes from 37% up to about 85% as the graph reweights edges and learns words."
5. (2:10) Show the 4-panel chart. "Five seeds. Baselines: the same graph frozen, a plain keyword table, and the table given the same feedback. Graph ends at 87% vs 74%, reaches 70% in 3 rounds vs 6. F1 is nearly tied, I'll be upfront about that."
6. (2:40) Caveats and pitch: "Synthetic benchmark, oracle feedback. Buyers are teams running agents across many MCP servers; the product is a hosted context layer that gets cheaper and sharper with use."

Upload to Loom, set the link to anyone-with-link, copy it.

## Step 4. Social post (go-to-market, 10 min)

Post on X **and** LinkedIn, tag HydraDB's official account (use the exact handle shown in the hackathon Discord/website), attach `results/curve.png` and a 30-second screen recording or the Loom link.

Draft (edit freely, keep the caveat, it builds trust):

> Agents shouldn't carry every tool in their prompt. I built ToolGraph on @HydraDB (self-hosted, open source): skills, MCP tools, dependencies and resolved entities live in a graph; the agent pulls only what the task needs, then the graph rewrites itself from what the agent actually used.
>
> Held-out task success: 37% -> 87% over 12 rounds (5 seeds). A plain keyword table given the same feedback reaches 74%. ~95% fewer tool tokens than preloading, p99 < 90 ms with 16 concurrent agents.
>
> Caveat: synthetic benchmark, oracle feedback. Live demo + code + Loom: <links>
>
> Built for the HydraDB hackathon.

## Step 5. Submit the form (before 5 PM)

Form: https://forms.gle/9brHhy7BC229tw868. You'll need: live link, GitHub repo link, Loom link, and likely the social post link. Submit by ~4:30 PM to leave a buffer. The Track 1 / Track 2 entries are due 11 PM, but the Discord message said "decode further instruction": **ask in the Discord what exactly is needed for the track submission**, since it was unclear to me. I built for Track 2.

## Step 6. Bounty 2 ($, due 11 PM IST)

Write a short X article or blog post: what you built, how HydraDB helped, the technical challenges. Real challenges from this project you can use:
- HydraDB rejected properties on batch `UNWIND ... CREATE` edges, so learned weights are written with one `CREATE` per edge and updated with `SET r.hit = ...`.
- `MERGE ... SET` only works in the `UNWIND` form; wipes must be by label.
- The node aborts on first query without `RUST_MIN_STACK=33554432`.
- Inferring the skill from tool overlap tied (`send_email` is in two skills), so feedback now carries the skill followed.
- The honest finding: the graph's advantage is recall and learning speed, not precision.

Submit via https://forms.gle/i8ybFDVciMThjhGbA with the article link plus the tweet link.

## Bounty 1 (skip unless you find a real bug)

They want a genuine issue + PR on `hydra-db/selfgraph`, explicitly not AI-generated filler. Don't submit something you haven't read and reproduced yourself.
