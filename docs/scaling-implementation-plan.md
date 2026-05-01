# Scaling Implementation Plan — $6 Droplet

Concrete changes to take Bibliotype's $6 DigitalOcean droplet from "barely handles 30 users" to "comfortably handles 100–200 DAU." Total time: ~2 hours.

> Companion doc: [`SCALING.md`](./SCALING.md) — capacity reference and when to upgrade.

Every change below is independent and reversible. Do them in phases — Phase 1 alone gets you 80% of the benefit.

---

## Phase 1 — Application changes (1 hour, biggest wins)

### 1.1 Gunicorn: switch to threaded workers

**File:** `docker-compose.prod.yml:30`

Replace:
```yaml
command: ["poetry", "run", "gunicorn", "bibliotype.wsgi:application", "--bind", "0.0.0.0:8000"]
```

With:
```yaml
command:
    - poetry
    - run
    - gunicorn
    - bibliotype.wsgi:application
    - --bind=0.0.0.0:8000
    - --workers=2
    - --worker-class=gthread
    - --threads=2
    - --timeout=60
    - --graceful-timeout=30
    - --keep-alive=5
    - --max-requests=1000
    - --max-requests-jitter=100
    - --access-logfile=-
```

**Why:** 2 workers × 2 threads = 4 concurrent requests instead of 1. `--max-requests=1000` recycles workers to prevent memory creep. `--timeout=60` kills hung requests before they pile up.

**Why 2, not 4 workers:** 4 × ~100 MB = 400 MB just for Gunicorn on a 1 GB box. 2 leaves room for everything else.

### 1.2 Database connection reuse

**File:** `bibliotype/settings.py`, after line 77

Change:
```python
DATABASES = {"default": dj_database_url.config(default=f'sqlite:///{os.path.join(BASE_DIR, "db.sqlite3")}')}
```

To:
```python
DATABASES = {
    "default": dj_database_url.config(
        default=f'sqlite:///{os.path.join(BASE_DIR, "db.sqlite3")}',
        conn_max_age=600,
        conn_health_checks=True,
    )
}
```

**Why:** Reuses Postgres connections for 10 minutes instead of opening a new one per request. Eliminates a major source of latency under load.

### 1.3 Celery task time limits

**File:** `bibliotype/settings.py`, near the Celery config (around line 194)

Add:
```python
CELERY_TASK_SOFT_TIME_LIMIT = 600   # 10 minutes — task gets SoftTimeLimitExceeded
CELERY_TASK_TIME_LIMIT = 900        # 15 minutes — worker is killed
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Don't hoard tasks; better fairness
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50 # Recycle worker after 50 tasks (frees memory)
```

**Why:** A runaway DNA task (e.g., a 10k-book CSV that hits an API timeout loop) currently has no upper bound and can hold the single Celery worker forever. Prefetch=1 ensures fairness across users.

### 1.4 Reduce frontend polling: 3s → 10s

**Files:**
- `core/templates/core/task_status.html:126`
- `core/templates/core/dashboard.html:141, 144, 222, 323`

Find every `setInterval(..., 3000)` (and the one `5000` for enrichment status) and change them:
- Status polling (`task_status.html`, `dashboard.html` lines 141/144/222): `3000` → `10000`
- Enrichment polling (`dashboard.html:323`): `5000` → `15000`

**Why:** Cuts status request volume by ~3.3x. With 50 active users, drops from ~1,000 req/min to ~300 req/min.

**UX impact:** worst case, user waits an extra 7 seconds to see "DNA ready" — imperceptible during a 30–60s task.

### 1.5 Rebuild and deploy

```bash
# Locally — verify gunicorn command parses correctly
docker-compose -f docker-compose.local.yml up --build -d
docker-compose -f docker-compose.local.yml exec web ps aux | grep gunicorn  # Should show 2 workers
docker-compose -f docker-compose.local.yml down

# Push and let CI/CD deploy
git add docker-compose.prod.yml bibliotype/settings.py core/templates/
git commit -m "Scale up: gunicorn threads, DB pooling, polling backoff, celery time limits"
git push origin main
```

---

## Phase 2 — VPS hardening (45 minutes)

SSH into the droplet for these.

### 2.1 Add 1 GB swap (OOM safety net)

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Tune swappiness — only swap when truly necessary
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Verify
free -h     # Should show 1 GB swap
```

**Why:** On 1 GB RAM, a single concurrent CSV processing spike + Gemini call can briefly push past available memory. Without swap, the OOM killer will (probably) kill Postgres or Celery — catastrophic. With swap, you get latency instead of crashes. `swappiness=10` keeps things fast in the normal case.

### 2.2 nginx tuning

**File:** `/etc/nginx/sites-available/bibliotype` (or whatever your site config is named)

Inside the `server { ... }` block:
```nginx
# Upload size — must match Django's 10MB cap in core/views.py:678
client_max_body_size 10m;

# Compress responses (especially the dashboard's chunky JSON)
gzip on;
gzip_vary on;
gzip_min_length 1024;
gzip_types text/plain text/css text/javascript application/json application/javascript;

# Aggressive caching for hashed static assets (Django ManifestStaticFilesStorage)
location /static/ {
    alias /path/to/your/staticfiles/;  # Match the Docker volume mount
    expires 1y;
    add_header Cache-Control "public, immutable";
    access_log off;
}

# Rate limiting — protects against polling abuse and scrapers
limit_req_zone $binary_remote_addr zone=app:10m rate=15r/s;
location / {
    limit_req zone=app burst=30 nodelay;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 90s;
}
```

Apply:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

**Why:**
- `client_max_body_size 10m`: matches Django's app-level cap (`core/views.py:678`). Without this, large uploads silently 413.
- `gzip`: dashboard JSON is 50–200 KB — compresses to ~10–30 KB.
- Static caching: hashed filenames mean immutable forever; saves repeat bandwidth.
- Rate limiting: 15 req/s with burst 30 is generous for legitimate users (each user generates ~6 req/min after Phase 1.4) but blocks abusive scrapers.

### 2.3 Redis memory cap

**File:** `docker-compose.prod.yml`, in the `redis` service

Replace:
```yaml
redis:
    image: redis:7-alpine
    healthcheck:
        ...
    restart: unless-stopped
```

With:
```yaml
redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 100mb --maxmemory-policy allkeys-lru --save ""
    healthcheck:
        ...
    restart: unless-stopped
```

**Why:** Bounds Redis at 100 MB. When full, evicts least-recently-used keys (the recommendation/community caches will rebuild on next request). `--save ""` disables RDB snapshotting (you don't need persistence — it's all derivable from Postgres).

### 2.4 Postgres tuning for small RAM

**File:** `docker-compose.prod.yml`, in the `db` service

Add:
```yaml
db:
    image: postgres:15
    command:
        - postgres
        - -c
        - shared_buffers=128MB
        - -c
        - effective_cache_size=384MB
        - -c
        - work_mem=4MB
        - -c
        - maintenance_work_mem=32MB
        - -c
        - max_connections=50
    # ...rest unchanged
```

**Why:** Postgres defaults assume a much larger machine. On 1 GB total, we want Postgres to use ~150–200 MB total. `max_connections=50` matches our actual usage pattern (4 web threads + 2 celery workers ≈ 6 connections steady state, with headroom for spikes).

### 2.5 Container memory limits (light touch)

**File:** `docker-compose.prod.yml`

Add `mem_limit` to each service:
```yaml
db:
    image: postgres:15
    mem_limit: 280m
    # ...

redis:
    image: redis:7-alpine
    mem_limit: 130m
    # ...

web:
    build: .
    mem_limit: 350m
    # ...

worker:
    build: .
    mem_limit: 280m
    # ...
```

**Why:** Total = 1,040 MB which is barely under 1 GB but lets each container handle peaks without dragging down the whole droplet. **Critical:** these are *limits*, not reservations — if `worker` is using 100 MB and `web` needs 350, both are fine.

> **If you skip this:** containers can OOM each other under load. With swap (Phase 2.1), the impact is graceful degradation rather than crashes, so you can defer this to "after Phase 1 if I see issues."

### 2.6 Apply changes

```bash
# On the VPS, in the project directory
docker compose -f docker-compose.prod.yml up -d --force-recreate
docker stats   # Watch memory usage stabilize
```

---

## Phase 3 — Polish & operations (30 minutes)

### 3.1 Disk cleanup

The repo root has accumulated Django Silk profiling output locally (already in `.gitignore`, but eats disk). Clean periodically:

```bash
ls *.prof 2>/dev/null | wc -l    # See how many
rm -f *.prof
```

Worth adding to a `make clean` target or a periodic local cron if Silk runs often.

### 3.2 Log rotation

Docker logs grow unbounded by default. **File:** `/etc/docker/daemon.json` on the VPS:
```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

```bash
sudo systemctl restart docker
docker compose -f docker-compose.prod.yml up -d --force-recreate
```

Django app logs (`logs/bibliotype.log` per `settings.py:226`) — add to `/etc/logrotate.d/bibliotype`:
```
/path/to/bibliotype/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    copytruncate
}
```

### 3.3 Daily Postgres backup

```bash
mkdir -p ~/backups
cat > ~/scripts/backup-db.sh <<'EOF'
#!/bin/bash
set -e
DATE=$(date +%Y%m%d)
docker compose -f /path/to/docker-compose.prod.yml exec -T db \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > ~/backups/bibliotype-$DATE.sql.gz
# Keep last 14 days
find ~/backups -name 'bibliotype-*.sql.gz' -mtime +14 -delete
EOF
chmod +x ~/scripts/backup-db.sh

# Cron at 4 AM daily
(crontab -l 2>/dev/null; echo "0 4 * * * ~/scripts/backup-db.sh") | crontab -
```

For real safety, periodically `scp` backups off-site (S3, Backblaze, or your laptop).

### 3.4 Firewall

If not already done:
```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

### 3.5 Uptime monitoring

Free tier: [Uptime Robot](https://uptimerobot.com).
- Add a monitor for `https://yourdomain.com/` with 5-minute interval
- Add an alert contact (your email)
- Add a second monitor for `https://yourdomain.com/admin/login/` to catch DB failures (admin requires DB)

### 3.6 Resource monitoring cron

Lightweight self-monitoring without paying for anything:
```bash
cat > ~/scripts/healthcheck.sh <<'EOF'
#!/bin/bash
DISK=$(df / | awk 'NR==2 {print $5}' | tr -d '%')
MEM=$(free | awk 'NR==2 {printf "%.0f", $3*100/$2}')
if [ "$DISK" -gt 85 ] || [ "$MEM" -gt 90 ]; then
    echo "Bibliotype VPS warning: disk ${DISK}%, mem ${MEM}%" | \
        mail -s "VPS health warning" you@example.com
fi
EOF
chmod +x ~/scripts/healthcheck.sh

# Run every 30 minutes
(crontab -l 2>/dev/null; echo "*/30 * * * * ~/scripts/healthcheck.sh") | crontab -
```

(Requires `mail` configured — `apt install mailutils` and Postfix in "internet site" mode pointing at your existing Brevo SMTP.)

---

## Verification

After all phases applied, verify on the VPS:

```bash
# Should show 2 workers and the new flags
docker compose -f docker-compose.prod.yml exec web ps aux | grep gunicorn

# Should show ~700 MB used, ~250 MB free, swap available
free -h

# Should show all 4 containers within their mem_limit
docker stats --no-stream

# nginx config sanity
sudo nginx -T | grep -E 'client_max_body_size|gzip|limit_req'

# Test a CSV upload end-to-end and time it
curl -w "%{time_total}\n" -o /dev/null -s https://yourdomain.com/dashboard/
```

Expected results after Phase 1+2:
- 4 simultaneous `curl https://yourdomain.com/` complete in parallel (not serialized)
- A 2 MB CSV upload returns within 1 second (task processes in background)
- Dashboard JSON response is gzipped (`Content-Encoding: gzip` in response headers)
- `vmstat 5` during normal use shows `si`/`so` columns at 0 (not swap thrashing)

---

## What this plan deliberately doesn't do

- **PgBouncer** — overkill at this scale; `CONN_MAX_AGE` is sufficient.
- **WhiteNoise** — nginx serves static fine; switching adds risk for no benefit at this size.
- **CDN (Cloudflare)** — worth considering for free DDoS protection, but adds complexity. Defer until you see actual scrape/abuse traffic. (Caveat: you already use Turnstile, so CF account exists.)
- **Sentry / APM** — PostHog catches exceptions and the LOGGING config writes to disk. Add proper APM at the $24+ tier when bug volume justifies the noise.
- **Multi-Celery-worker on $6** — RAM doesn't allow it. Defer to $12+ tier.

These all become relevant on the next tier — see [`SCALING.md`](./SCALING.md) for when.

---

## Rollback

Every change is independently reversible:

| Change | Rollback |
|---|---|
| Gunicorn flags | Revert `docker-compose.prod.yml`, redeploy |
| `CONN_MAX_AGE` | Remove from settings.py |
| Polling intervals | Revert template changes |
| Swap | `sudo swapoff /swapfile && sudo rm /swapfile`, remove fstab line |
| nginx config | Comment out new directives, `nginx -t && systemctl reload` |
| Redis maxmemory | Remove `command:` line from compose |
| Postgres tuning | Remove `command:` block from compose |
| Memory limits | Remove `mem_limit:` lines |

If something breaks, work backwards one change at a time — don't revert everything at once or you lose the diagnosis.

---

## Time budget summary

| Phase | Time | Risk | Reversibility |
|---|---|---|---|
| 1. App changes | 1 hr | Low (tested in CI) | Trivial (git revert) |
| 2. VPS hardening | 45 min | Medium (live VPS) | Easy (described above) |
| 3. Polish & ops | 30 min | Low | N/A (additive) |
| **Total** | **~2 hr 15 min** | | |

Phase 1 alone gets you ~80% of the capacity gain. Phases 2 and 3 are about resilience and operability — they prevent rare-but-catastrophic failures rather than improving everyday performance.
