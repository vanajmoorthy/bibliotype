# Scaling Reference

How much load Bibliotype can handle at each VPS tier, where it breaks first, and what to do about it.

> Companion doc: [`scaling-implementation-plan.md`](./scaling-implementation-plan.md) — the concrete code/config changes to actually apply the recommendations below.

---

## Capacity by tier

| Tier | RAM / vCPU | Cost | Sustainable DAU | Concurrent browsers | Concurrent uploads | Daily uploads (worst case / steady state) |
|---|---|---|---|---|---|---|
| **$6 stock** (no changes) | 1 GB / 1 | $6/mo | 20–40 | 5–15 | 1 in-flight | 10 / 30 |
| **$6 tuned** (all Phase 1–3 changes) | 1 GB / 1 | $6/mo | **100–200** | **30–50** | 1 in-flight, queue OK | **15 / 100–150** |
| **$12 tuned** (2 GB / 1–2 vCPU) | 2 GB / 1–2 | $12/mo | 300–500 | 80–120 | 2 in-flight | 20 / 300 |
| **$18 tuned** (2 GB / 2 vCPU) | 2 GB / 2 | $18/mo | 500–1,000 | 150–250 | 2–3 in-flight | 30 / 500 |
| **$24 tuned + managed PG** | 4 GB / 2 vCPU + DB | ~$39/mo | 2,000–5,000 | 400–600 | 4 in-flight | 50 / 1,500 |
| **Multi-droplet** (web+celery split, managed PG/Redis) | varies | $60+/mo | 10k+ | 1k+ | 8+ in-flight | 100+ / 5,000+ |

**"Worst case daily uploads"** = users with mostly brand-new books not yet in the shared Book pool. **"Steady state"** = once the Book/Author pool is warm (~200+ users uploaded), most books are dedup'd and skip API enrichment entirely.

**Hard ceiling regardless of tier:** Google Books free quota is **10,000 calls/day**. With dedup working well, this rarely binds. With many cold uploads in a single day, it does.

---

## Where it breaks first (in order)

1. **Gunicorn worker exhaustion** — default 1 sync worker, every request blocks. Fixed by Phase 1.
2. **OOM during CSV processing** — pandas peaks at ~10x file size. Fixed by swap + memory limits + worker recycling.
3. **DB connection churn** — no `CONN_MAX_AGE`, every request opens a new Postgres connection. Fixed by Phase 1.
4. **Frontend polling DDoS** — 3s polling × 50 users = ~1,000 req/min just for status. Fixed by Phase 1.
5. **Google Books 10k/day quota** — only matters at scale with cold caches. Mitigated by cache warmth, eventually requires paid key.
6. **Postgres `max_connections=100`** — only an issue at 500+ DAU. Fixed by adding PgBouncer or moving to managed PG.
7. **Single Celery worker** — uploads queue serially. Fixed by adding a 2nd worker container at $12+ tier.

---

## Scaling ladder — when to move up

### Stay on $6 if all of these are true
- Memory usage stays below 80% of 1 GB during normal traffic
- p95 response time on `/dashboard/` is under 800 ms
- CSV upload → DNA dashboard takes under 90 seconds end-to-end
- Less than ~20 simultaneous unique visitors
- No swap thrashing (check with `vmstat 5`, `si`/`so` columns near zero)

### Move to $12 (2 GB / 1 vCPU) when…
- **Trigger:** memory consistently >85%, or you see OOM kills in `dmesg`
- **Cost:** +$6/mo
- **Effort:** 30 seconds in DO console (resize, no downtime)
- **What changes:** bump Gunicorn to 3 workers, Celery to 2 workers, Redis maxmemory to 200MB
- **New capacity:** ~300–500 DAU

### Move to $18 (2 GB / 2 vCPU) when…
- **Trigger:** CPU consistently >70% on $12, or DNA generation taking >60s wall time
- **Cost:** +$6/mo over $12
- **Effort:** 30-second resize
- **What changes:** Celery concurrency=2 actually parallel (not just thread-switched), 4 Gunicorn workers
- **New capacity:** ~500–1,000 DAU

### Add managed Postgres ($15/mo) when…
- **Trigger:** DB query p95 >100ms, or Postgres process using >40% of droplet RAM, or planning >1,000 DAU
- **Cost:** +$15/mo
- **Effort:** 1 hour (provision, restore, swap `DATABASE_URL`, decommission `db` container)
- **What changes:** removes Postgres from app droplet → all RAM available for app, automatic backups, connection pooling included
- **New capacity:** ~2,000–5,000 DAU on the $24 + managed PG combo

### Split web and Celery onto separate droplets when…
- **Trigger:** heavy CSV processing visibly degrading web responsiveness, or planning >5,000 DAU
- **Cost:** +$12–$18/mo for second droplet
- **Effort:** 2–4 hours (replicate Docker setup, point Celery at shared Redis, ensure media/static are shareable)
- **What changes:** web droplet handles only HTTP, dedicated Celery droplet does enrichment + AI calls
- **New capacity:** 5,000–10,000+ DAU

### When Google Books starts 429-ing
- **Phase A** (cheap): increase reliance on Open Library, cache enrichment results indefinitely (currently they don't expire — verify `core/services/book_enrichment_service.py`)
- **Phase B** (paid): Google Books paid tier is ~$0.50 per 1,000 calls; 100k/day = $50/mo. Cheaper than skipping enrichment.
- **Phase C** (architectural): pre-enrich popular books in a background job from a curated list (NYT bestsellers, Goodreads top-1000) so cold uploads hit cache immediately.

---

## Cost-per-1k-DAU summary

| Tier | Monthly | DAU capacity | $/1k DAU |
|---|---|---|---|
| $6 tuned | $6 | 200 | $30 |
| $12 tuned | $12 | 400 | $30 |
| $18 tuned | $18 | 800 | $22.50 |
| $24 + managed PG | $39 | 3,500 | $11 |
| Multi-droplet + managed | $60+ | 10,000 | $6 |

The cliff is at **managed Postgres** — it's where unit economics flip from "personal project" to "small product."

---

## Monitoring thresholds

Watch these on the VPS to know when to act:

| Metric | Healthy | Warning | Migrate now |
|---|---|---|---|
| RAM usage | <70% | 70–85% | >85% sustained |
| Swap in/out (`vmstat`) | 0 / 0 | <100 KB/s | >1 MB/s sustained |
| `/` disk free | >50% | 30–50% | <20% |
| Postgres connections | <20 | 20–50 | >50 |
| p95 dashboard latency | <500 ms | 500–1,500 ms | >1,500 ms |
| Celery queue depth | <5 | 5–30 | >30 sustained |
| Google Books quota used | <50% | 50–80% | >80% |

Cheapest monitoring stack for $6 tier: **Uptime Robot** (free, HTTP checks every 5 min) + a daily cron emailing `free -h && df -h` to yourself + PostHog (already integrated) for app-level errors.

---

## What this doc is not

- A runbook for incident response (see ops notes in `ARCHITECTURE.md`)
- A guide to optimizing specific slow queries (use Django Silk locally, profile real traffic)
- A guarantee of these numbers under your specific load — they're estimates based on code review of `bibliotype/settings.py`, `docker-compose.prod.yml`, `core/tasks.py`, and the celery/caching deep-dive rules. Real numbers come from production telemetry.
