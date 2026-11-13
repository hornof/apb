# System design guide for the TDD

Use this when you write the "Stack & architecture", "Test plan" and "Risks" sections of an apb TDD. It walks you through the design decisions in order. Each step has questions to ask, the usual answer for a small project, and what to write down. Skip any step that does not change a decision, and write one line saying you skipped it and why.

## 1. When to use this, and how small to go

Use it after PRD gate 1, before you draft the TDD. The designer can be Claude, the human, or both together.

Scale it to the project:

| Project size | Example | How to use this guide |
|---|---|---|
| Small | A personal website, a command-line tool, a browser extension | One line per step. Most answers are "the host handles it" or "not needed, because ...". |
| Medium | A logged-in web or phone app with a database, a few hundred users, one outside service | Two to five lines per step. Name every store and every external service. |
| Large | Many users writing at once, money, health data, real-time updates, several customers sharing one system | Full answers, plus a reviewer read of the design before gate 2. Get a person with production experience to look at it. |

Rule for every size: each component in the design must trace to a PRD requirement or a number in step 2. If you cannot say which, remove it.

## 2. The steps

### Step 1. Recap the requirements from the PRD

Ask:
- What are the two to five things a user does? (Functional.)
- Which PRD checks are about speed, uptime, data loss, privacy or cost? Those become quality targets in step 7.
- What is out of scope? Copy it from the PRD so nobody designs for it.
- Who operates this after launch, and what can they run? Someone who only knows a hosting dashboard and an experienced operations engineer need different stacks.

For a small project, usually: copy the PRD's user actions and out-of-scope list. Note that the operator is the project owner, working from a laptop.

Write in the TDD: a short list of user actions, each tagged with its PRD check ID, plus the out-of-scope list.

### Step 2. Rough numbers

Do this only far enough to change a decision. The answer is usually "this fits on one machine" or "this fits in the free tier", and that is a useful finding.

Ask:
- How many users, how many actions each per day? Requests per second = daily actions ÷ 100,000 (a day is 86,400 s). Peak is 2 to 3 times that, 10 times for launches.
- Ratio of reads to writes?
- How much data after a year? Records × bytes per record. Count large files (images, video) separately.
- Biggest single file or request?
- Does any number hit a host limit (file count, file size, request time, free-tier quota)?

If the product runs on the user's own machine (a command-line tool, a library, a desktop app), skip the traffic numbers; only the size of the input and the time a run takes matter.

For a small project, usually, for example a club sign-up form: "about 50 visitors a day, 300 sign-ups a year at about 1 KB each. Well inside the free tier." Done.

Write in the TDD: three or four numbers and the decision each one drives ("300 records, under 1 MB: one small database table, no file storage").

Handy anchors:

| Thing | Rough value |
|---|---|
| Read from memory | 100 ns |
| Random read from SSD | 16 to 150 µs, depending on hardware |
| Round trip inside one datacenter | 0.5 ms |
| Round trip across a continent | 70 ms |
| Round trip across an ocean | 150 ms |
| 99.9% uptime | 8.8 hours of downtime a year |
| 99.99% uptime | 53 minutes of downtime a year |
| 1 Gbps link | about 100 MB/s in practice |

### Step 3. Pick the simplest stack that works

Start at the top of the ladder that fits the product, and stop at the first rung that meets every PRD check.

For products that run online (websites, web apps, APIs):

1. Static files on static hosting (Cloudflare Pages, GitHub Pages, Netlify). Content changes by commit.
2. Static site plus a few serverless functions (Cloudflare Workers or Pages Functions, Vercel or Netlify Functions) for forms or small APIs.
3. Above, plus a managed store (Cloudflare D1, KV or R2; Supabase or Neon Postgres; Turso).
4. One app server plus one managed Postgres (Fly.io, Render, Railway).
5. Several services, queues, caches. Only with numbers from step 2 that demand it.

For products that run on the user's own machine (command-line tools, libraries, desktop and phone apps):

1. One program using only the language's built-in libraries.
2. Plus a few well-known libraries.
3. Plus local storage: files, or a small built-in database such as SQLite.
4. Plus an online service, only if users must share data.

Ask:
- Which rung meets all the PRD checks?
- Can the operator from step 1 deploy, debug and pay for it?
- Can the reviewer try each PR before it's merged: a preview address for a site, a test build for an app, an installable package for a tool? apb's review step depends on it.
- Is there a vendor or language the human already uses? Prefer it.

For a small project, usually: rung 1 or 2. One monolith, no microservices.

Write in the TDD: the rung, the named products, and one sentence on why the rung above it is not enough.

### Step 4. Architecture sketch

Ask:
- What are the boxes? (Browser, host/CDN, function, store, external API.)
- What flows on each arrow, and is it synchronous or in the background?
- Trace one write and one read from the user's click to the stored data and back. Can you say each in one sentence?
- Where does the trust boundary sit? Everything on the client side of it is untrusted input.

For a small project, usually one line. A website: `browser → static host (HTML, images)`. A command-line tool: `terminal → tool → files on disk`.

A Claude Whiteboard page is a good place to work out the sketch with the human before it goes in the TDD.

Write in the TDD: a text diagram, or a Mermaid diagram (a text format GitHub draws as a picture), with labeled arrows, plus the one-sentence read path and write path.

### Step 5. Data model and APIs

Ask:
- What are the entities (about five is plenty) and how do they relate?
- What are the two or three reads the product does most? Design the data for those.
- For each operation: name, input, output, error cases. Most small systems have two to four operations.
- Can a write be retried safely? If a retry would double something (a payment, an email, a record), give each request a unique ID so a repeat is ignored (an idempotency key), or make it an upsert (insert or update in one step).
- Does any list grow without limit? Then return it in pages.
- How will the schema change later? Add fields; do not rename or delete live ones.

For a small project, usually: a single data file or one small table, and no online API at all. For a command-line tool or library, the "API" is its commands or functions: arguments, checks, output.

Write in the TDD: the entity list with fields and types, the operations with inputs and outputs, and where the schema is validated.

### Step 6. Where data lives

Name a real store for every piece of data. Don't default to a local file or in-memory data: tests pass, but many hosts wipe both on every deploy or restart, and the data is gone.

Ask, for each kind of data:
- Which named store holds it? (Git repo, D1, Postgres, R2/S3, KV, browser localStorage.)
- Who writes it, who reads it?
- What happens to it on redeploy? When a server function starts fresh?
- For data on the user's machine: what happens when the app is updated or uninstalled, or the phone is replaced?
- How is it backed up, and has a restore been tried once?
- How much loss is acceptable (RPO) and how long can restore take (RTO)?
- Is any of it personal data? Where is it, and how is it deleted on request?

Defaults:

| Data | Small-project default |
|---|---|
| Content the owner edits | Git repo, deployed by the host |
| Large files (images, video, uploads) | Repo if small and few; object storage (R2, S3) otherwise |
| Structured records users create | Managed SQL (D1, Supabase/Neon Postgres, Turso) |
| Simple lookups, flags, counters | KV store (Cloudflare KV, Upstash Redis) |
| Sessions, auth | The auth provider's own store |
| Per-viewer preferences | localStorage, only for things that can be lost |
| Data an app keeps on the user's machine | A file or SQLite in the system's app-data folder |

For a small project, usually: "all data lives in the Git repo; GitHub is the backup; RPO is the last push." One line.

Write in the TDD: a table of data kind → store → backup → loss tolerance.

### Step 7. Quality targets

Turn each relevant non-functional requirement into a number a test or a check can confirm. Tie each one to a PRD check. Skip the ones the PRD does not care about, and say so.

| Target | Question | Small-project answer | How it gets checked |
|---|---|---|---|
| Latency | How fast must the main screen or action be, for 95 (or 99) out of 100 users? Averages hide the slow ones. | "Main screen usable in under 2 s on a phone connection" | Online: Lighthouse (a page-speed checker) or a timed Playwright run (a tool that drives a browser in tests) against the preview address. On the user's machine: time a run on a typical input in a test. |
| Throughput | Peak requests per second from step 2? | "Host handles it" | Not tested; note the host's limit |
| Availability | What uptime does the PRD need? Never promise more than the host's own figure. | "Whatever the host gives" | Not tested |
| Durability | What data can be lost, and how much? | "Repo is the source of truth" | One restore drill, written up once |
| Consistency | After a write, must every reader see it at once, or is a delay fine? Must users see their own writes immediately? | "New content appears after the next release" | Check of the live product after each release |
| Security | Who may do what? See step 9. | "Anyone can read; only the owner can change content" | Test that changing content needs the owner's credentials |
| Cost | Monthly ceiling? See step 10. | "$0, free tier" | Host usage page, checked after launch |
| Operability | Can the operator see errors and roll back? | "Host's deploy log; roll back by reverting the commit" | One rollback tried before launch |

Two cautions from Google's *Site Reliability Engineering* book: do not set a target just because the system meets it today, and start loose and tighten later. Keep the list short.

Write in the TDD: the filled table, rows the PRD does not need marked "n/a".

### Step 8. Failure modes: what breaks first

Ask:
- Which external dependency fails most often (an outside API, an AI model provider, an email service)? What does the user see when it is down or slow? Every external call needs a timeout.
- Which limit do you hit first as usage grows (free-tier quota, file count, database size, rate limit)?
- What happens with bad input: an empty field, a huge file, a duplicate name, a wrong file type?
- What happens if a background job runs twice, or not at all?
- If a deploy is broken, how do you roll back, and how long does it take?
- If you publish a package or an app, a broken release can't just be reverted. Can you publish a fixed version quickly? App stores can take days to review one.
- If retries pile up on a sick service, what stops them? (Wait longer after each retry, add a little randomness so clients don't all retry at once, and stop after a few tries.)

For a small project, usually: "the host is down: nothing to do. Bad input breaks the build or crashes the tool: check input first and show a clear error. A broken deploy: revert the commit."

Write in the TDD, in "Risks": the top three failures, the effect on users, the mitigation, and the check that catches each one early.

### Step 9. Security and secrets

Ask:
- Who are the users and roles? What can each one do? (Authentication: who you are. Authorization: what you may do.)
- Use a hosted auth provider (Cloudflare Access, Clerk, Supabase Auth, Auth0) instead of building login.
- Where do secrets live? In the host's secret store or environment variables, never in the repo, never in code that ships to users (web pages, apps, packages): anyone can dig it out. Which secrets exist, and who can replace them if they leak?
- What input crosses the trust boundary? Validate it on the server side.
- Can one user ever see another user's data? If so, test that they cannot.
- Is there personal data? What is the minimum you need to keep?
- If an AI model reads user content, treat that content as untrusted. It must not be able to trigger tools or reach secrets.

For a small project, usually: "no logins, no user data, no secrets. The host deploys straight from GitHub, so there's no deploy token to store." A published package's upload token lives in GitHub's secret store.

Write in the TDD: roles and permissions, the list of secrets and where each is stored, and the security tests in the test plan.

### Step 10. Cost

Ask:
- What does it cost per month at the step-2 numbers, and at 10 times those numbers?
- Which item scales with usage (bandwidth, function calls, AI model usage, database rows)?
- Is there a hard cap or a billing alert? Set one before launch.
- For AI model calls: is there a per-run cap on steps, tokens and time?
- For products on the user's machine: app store fees (Apple charges yearly, Google once) and code-signing certificates.

For a small project, usually: "$0 on the free tier; the domain costs about $10 a year."

Write in the TDD: the monthly estimate, the item that grows fastest, and where the cap or alert is set.

### Step 11. Testing hooks

The TDD's test plan maps 1:1 to the PRD's checks. Design so each part can be tested on its own.

Kinds of test, from smallest to biggest:
- **Unit test:** checks one function on its own.
- **Integration test:** checks parts working together, with a real database.
- **End-to-end test:** uses the whole product the way a person would, for example by driving a browser or running the tool from the terminal.
- **Smoke test:** a quick check, after each release, that the basics of the live product work.

Ask:
- For each PRD check: which test proves it, at which level, and where does it run (on the PR's preview build, or on the live product)?
- Can tests use a local copy of the real database (SQLite, a Postgres container) instead of a fake? With a fake, nothing proves the data is really saved.
- Where does each outside service plug in? Tests can swap in a fake stand-in there, plus one test that calls the real service to make sure it still behaves as expected.
- Which rules hold for any input? ("Every item the index lists actually exists", "adding then removing an item leaves the data unchanged".) Each becomes a property-based test: a test tool tries many random inputs against the rule.
- Is each action tested in both directions? Add then remove, undo then redo, forward then back.
- Would the tests catch a bug? Mutation testing plants small bugs in the code on purpose and checks that some test fails each time.
- What does the smoke test of the live product look at?

For a small project, usually: unit tests for the core logic, one end-to-end test of the main thing a user does, and a smoke test of the live product after each release.

Write in the TDD: the test plan table, PRD check → test → level → where it runs.

## 3. Common tools and when to reach for them

Rules of thumb, adapted from Dinesh Varyani's widely reposted "Golden Rules" list for system design interviews:

- Read-heavy? Add a cache.
- Slow responses for users far away? Add a CDN: copies of your files on servers around the world.
- Write-heavy or slow work? Put a queue in front and process in the background.
- Changes that must all happen or none at all (transactions)? Use a relational database.
- Images, video, files? Use object storage.
- Searching lots of text? Use a search index.
- One part whose failure takes everything down? Add a second copy that takes over.

Each rule needs a measured or estimated reason from step 2. On a small project, the host already gives you most of them.

For products on the user's machine, the usual equivalents: save results on disk instead of recomputing them; do slow work in the background so the app stays responsive; use SQLite for transactions and for text search.

| Need | Tool | Examples | Skip it when |
|---|---|---|---|
| Serve files fast everywhere | CDN | Cloudflare, Fastly, CloudFront; built into Pages, Netlify, Vercel | Your static host already does it (most do) |
| Host a site with no server | Static hosting | Cloudflare Pages, GitHub Pages, Netlify | You need per-request logic or user data |
| A little server logic | Serverless functions | Cloudflare Workers, Vercel/Netlify Functions, AWS Lambda | You need long-running work, local state, or a persistent connection |
| Structured records, transactions | Relational database | Postgres (Supabase, Neon), SQLite (D1, Turso) | The data is a handful of files the owner edits by hand |
| Lookups by ID, flags, counters | Key-value store | Cloudflare KV, Upstash Redis, DynamoDB | You need queries across fields, or every reader must see a change at once (in Cloudflare KV, a change can take a while to show everywhere) |
| Big files, uploads | Object storage | R2, S3, GCS | A few small files that fit in the repo |
| Faster reads of the same data | Cache | HTTP cache headers, CDN cache, Redis | Reads are already fast, or stale data would be wrong. Every cache needs an invalidation rule. |
| Slow queries | Database index | `CREATE INDEX` on the filtered or sorted column | The table is small; every index slows writes |
| Slow or bursty work | Queue + worker | Cloudflare Queues, SQS, a jobs table in Postgres | The work finishes within the request time limit |
| Scheduled work | Cron / scheduled job | Cloudflare Cron Triggers, GitHub Actions schedule | Nothing needs to run on a timer |
| Full-text or faceted search | Search index | Postgres full-text, SQLite FTS5, Meilisearch, Typesense, OpenSearch | Fewer than a few thousand items: filter in the browser |
| Spread load over servers | Load balancer | Built into every managed host | You have one server or use serverless |
| Stop abuse of an endpoint | Rate limiter | Cloudflare rate limiting rules, a counter in Redis | No public write endpoints |
| More read capacity on one database | Read replicas | Managed Postgres replicas | One database is not yet busy. Replicas lag, so reads can be stale. |
| Data too big for one database | Split the data across databases (sharding) | By tenant or user ID | Almost always on apb projects. Revisit at hundreds of GB or thousands of writes per second. |
| Server pushes updates to the browser | A live connection (WebSocket or SSE) | Durable Objects, Supabase Realtime, Pusher | Polling every few seconds is good enough |
| Login | Auth provider | Cloudflare Access, Clerk, Supabase Auth, Auth0 | No logins (public read-only site) |
| Secrets | Secret store | Host env vars, GitHub Actions secrets, Doppler | Never skip if there are secrets |
| Know when it breaks | Logs, error tracking, uptime check | Host logs, Sentry, an uptime pinger | Never fully skip; on a small site, the host's deploy log and one uptime ping are enough |

## 4. Red flags in designs Claude writes

Check the TDD and the first PRs for these:

- Data kept in a local file, or only in the program's memory, on a host that wipes both on redeploy or when a server function starts fresh.
- Tests that fake the database, so nothing proves the data is really saved.
- Parts with no PRD reason: Redis, Kafka, Kubernetes, microservices, GraphQL, for a site with 50 visitors.
- Secrets in the repo, in a settings file committed to the repo, or in code that ships to users.
- Works on the developer's machine, breaks elsewhere. Online: absolute paths, case-sensitive file names, missing environment variables, a different Node or Python version. On users' machines: a different operating system, missing permissions, no internet connection.
- Trusting the client: checks only in the browser or app, or prices and roles sent from the user's side.
- Outside calls with no timeout and endless retries.
- Retried writes that double-charge, double-send or duplicate records.
- Lists that return everything at once, with no pages.
- Quality targets with no test, or tests with no PRD check behind them.
- A stack the operator cannot run, debug or pay for.
- No rollback plan, and no backup that has ever been restored.
- Changing the shape of stored data (renaming or deleting fields) with no plan to convert existing data.
- AI model loops with no cap on steps, tokens or time.
- Outside libraries without fixed versions, so tomorrow's build differs from today's.

## 5. Going further

For more depth on each step:

- donnemartin/system-design-primer on GitHub: component overviews and latency tables.
- Martin Kleppmann, *Designing Data-Intensive Applications*: storage, replication, consistency.
- *Site Reliability Engineering* (Google), chapter "Service Level Objectives": how to set and measure targets.
- AWS Well-Architected Framework: six pillars (operational excellence, security, reliability, performance efficiency, cost optimization, sustainability) as a checklist for step 7.
