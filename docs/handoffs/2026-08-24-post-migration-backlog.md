# Handoff: post-migration backlog (2026-08-24)

> **Status 2026-09-08: ALL backlog items below are DONE** (verified live in prod
> and on the old box on 2026-09-08). Only "Launch prep" (item 6) remains, by
> design — it's gated on the launch decision. Resolutions:
>
> - **Google Books key 403** → fixed (key restriction updated); verified from
>   the prod web container: HTTP 200, `totalItems: 1`.
> - **Old box backup cron** → removed; only a docker-prune cron remains on
>   bibliotype-vps.
> - **Enrichment queue split** → PR #163 (implements plan v2 in full: sorted
>   queue order, prefetch 1, inline-enrichment skip for bulk, worker `-Q`
>   guard, `ignore_result=True`, routing tests). Verified: prod worker
>   consumes `celery` + `enrichment_bulk`.
> - **OL rate bump 30/m → 60/m** → shipped in the same PR #163.
> - **Staticfiles `static/src/` fix** → PR #166 moved the Tailwind source to
>   `tailwind/input.css`; entrypoint runs plain `collectstatic --noinput`.
> - **Prod Celery beat** → PR #167 added `-B -s /tmp/celerybeat-schedule` to
>   the worker (embedded beat — a separate beat container doesn't fit the 1GB
>   box, ~236MB available). Verified in prod logs: anonymize-expired-sessions
>   fires daily at 02:00, research-publisher-mainstream weekly.
> - **Old box cleanup** → done: `/home/bibliotype/app`, `~/backups-repo`, the
>   nginx bibliotype site, and its certbot renewal config are all gone;
>   mcow/puzzleflix untouched.
>
> The rest of the doc is kept as-is for the operational knowledge (SSH targets,
> Terraform state location, gotchas).

Context: production migrated today from the old shared droplet (68.183.38.239)
to a fresh Terraform-managed droplet. Everything below is either pending work
or operational knowledge the next agent needs.

## Current production state (all verified working)

- **bibliotype.app** serves from droplet `bibliotype-prod` (lon1, s-1vcpu-1gb,
  DO project "bibliotype"). Inbound via **reserved IP 159.223.244.72**
  (Cloudflare-proxied DNS + `DO_SSH_HOST` GitHub secret point there).
  **Outbound traffic uses the droplet's own IP 167.99.91.226** — matters for
  IP-restricted API keys.
- SSH: `ssh bibliotype-new` (user `bibliotype`, key `~/.ssh/id_ed25519_do`);
  root works with the same key for system-level work (bibliotype's sudo is
  scoped to the two staticfiles chown/chmod commands the deploy runs).
- Deploys: unchanged GitHub Actions SSH deploy, now targeting the new box —
  two green runs post-migration (PRs #157, #158).
- Gunicorn now runs `--workers 1 --threads 4 --worker-class gthread
  --timeout 60` (PR #158) — verified live.
- Terraform merged to main in `infra/`. **Live terraform.tfstate +
  terraform.tfvars (contains DO token) sit gitignored in the main checkout's
  `infra/` — do not lose them.** Launch-week resize = set
  `droplet_size = "s-2vcpu-2gb"` in tfvars, `terraform apply` (reversible,
  `resize_disk = false`).
- Nightly DB backup cron runs on the new box (3am, `~/backups-repo`,
  dedicated deploy key, backup.sh updated to `docker compose`).
- Health sweep 2026-08-24 from inside the prod web container: Postgres,
  Redis, Celery worker ping, Open Library (identified UA), Gemini
  (`gemini-flash-lite-latest`), Brevo SMTP login, PostHog EU event capture,
  Turnstile — all OK. Google Books: FAIL (see below).

## Pending — user actions (not agent-doable)

1. **Google Books API key 403s from the new box** — key is IP-restricted to
   the old droplet. Fix in Google Cloud Console → Credentials → the key →
   IP restrictions: add `167.99.91.226`. Until then GB enrichment silently
   contributes nothing (OL still works). Note: any droplet rebuild changes
   the outbound IP → restriction must be updated again (or switch the key to
   an API restriction instead of IP).
2. **Disable the old box's backup cron** (it backs up the dead DB nightly and
   its git pushes now conflict):
   `ssh bibliotype-vps 'crontab -l | grep -v backup.sh | crontab -'`

## Pending — implementation work (priority order)

### 1. Enrichment queue split (planned, reviewed, ready to build)

Plan: `docs/plans/2026-08-15-feat-bulk-enrichment-queue-plan.md` (v2 —
post-review; do NOT build v1). Worktree ready:
`.claude/worktrees/bulk-enrichment-queue` (branch `feat/bulk-enrichment-queue`,
clean at origin/main as of Aug 15 — rebase before starting).
Key review-mandated decisions baked into the v2 plan:
- `queue_order_strategy: "sorted"` NOT `"priority"` (celery#8673: priority
  order is hash-randomized per worker restart). Test asserts
  `"celery" < ENRICHMENT_BULK_QUEUE`.
- `CELERY_WORKER_PREFETCH_MULTIPLIER = 1`.
- `bulk_enrichment=True` must also SKIP inline enrichment in
  `calculate_full_dna` (else seed runs hammer OL/GB in-process, outside the
  Celery rate limit — the plan's whole point).
- `ignore_result=True` on `enrich_book_task` +
  `check_author_mainstream_status_task`.
- `celeryd_after_setup` guard: worker hard-exits if not consuming the bulk
  queue (forgetting `-Q` strands tasks silently).
- Same task name for bulk + interactive (rate_limit is per task NAME; a
  second name would double external API throughput).

### 2. Open Library rate bump 30/m → 60/m — NOW UNGATED

Was gated on confirming the identified User-Agent is live in prod. The
2026-08-24 health sweep confirmed it (OL 200 with
`BibliotypeApp/1.0 (contact: ...)` from the prod container). One-line change:
`rate_limit="60/m"` on `enrich_book_task` (core/tasks/enrichment.py). Fold
into the queue-split PR or ship separately after it.

### 3. staticfiles fix: `static/src/` must not be collectable

`collectstatic` under ManifestStaticFilesStorage fails on
`static/src/input.css` (`@import "tailwindcss"` → looks for literal
`src/tailwindcss`). Currently worked around with `--ignore src` (run
manually); the container entrypoint's collectstatic run needs checking too.
Proper fix: move `static/src/` out of `STATICFILES_DIRS` reach (relocate the
Tailwind source out of `static/`, adjust package.json build paths) or exclude
it structurally. Small PR.

### 4. Prod Celery beat is missing (pre-existing, discovered during review)

`CELERY_BEAT_SCHEDULE` in settings defines `anonymize_expired_sessions_task`
(daily) and `research_publisher_mainstream_task` (weekly), but prod runs no
beat (worker has no `-B`, no beat container) — **these have likely never run
in prod**. Decide: add `-B` to the single worker (simplest on 1GB) or a beat
container. Check RAM impact first.

### 5. Old box cleanup (after a stable week or so)

The old droplet (68.183.38.239, `ssh bibliotype-vps`) is KEPT — it still
runs mcow.ml + puzzleflix. Only remove the bibliotype pieces:
- `docker compose -f /home/bibliotype/app/docker-compose.prod.yml down -v`
- Remove `/home/bibliotype/app`, `~/backups-repo`, the nginx `bibliotype`
  site (sites-enabled/sites-available), certbot renewal config for
  bibliotype.app.
- Old droplet security holes seen in the Aug capacity review: MariaDB
  publicly bound on 0.0.0.0:3306, Plex on 32400 (Plex was removed; MariaDB
  binding may still need fixing — belongs to puzzleflix, user's call).

### 6. Launch prep (when the time comes)

- tfvars → `s-2vcpu-2gb`, `terraform apply` ($18/mo, reversible).
- After resize (2 vCPU): consider Celery `-c 2` in compose.
- Google Books key IP restriction will need the same droplet-IP check if the
  droplet was ever rebuilt.

## Gotchas the hard way taught us

- **cloud-init template (`infra/cloud-init.yaml.tftpl`) must be pure ASCII** —
  DO's metadata mangles multibyte chars; cloud-init then discards the ENTIRE
  config silently (first apply booted a bare Ubuntu box).
- **`/home/bibliotype` must be 755** — Ubuntu 24.04 creates homes 750 and
  nginx (www-data) can't traverse → every static asset 403s behind
  Cloudflare's cache. Fixed live + in cloud-init.
- Reserved IP covers inbound only; outbound = droplet IP (see GB key above).
- `docs/plans/` is gitignored (local working docs); handoffs like this one
  ARE committed.
