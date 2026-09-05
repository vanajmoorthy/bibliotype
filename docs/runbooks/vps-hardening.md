# VPS Hardening Runbook — manual steps on the droplet

Companion to [`../scaling-implementation-plan.md`](../scaling-implementation-plan.md).
These are the Phase 2–3 items that **cannot ship via the repo** — they're host-level
config on the DigitalOcean droplet. Everything repo-side (gunicorn flags, DB conn
reuse, Celery limits, Redis/Postgres tuning, polling backoff) ships with the
`chore: scaling hardening` PR and deploys automatically on merge.

Already covered — do NOT redo:

- **Swap (2 GB) + `vm.swappiness=10`** — cloud-init (`infra/cloud-init.yaml.tftpl`)
- **Firewall** — DigitalOcean cloud firewall in Terraform (`infra/main.tf`); UFW on the host is redundant
- **fail2ban, unattended-upgrades, SSH hardening** — cloud-init

SSH in as `bibliotype@159.223.244.72` for everything below.

---

## 1. nginx tuning (~10 min)

Edit the site config (`/etc/nginx/sites-available/<site>`; check `ls /etc/nginx/sites-enabled/`).
Inside the `server { }` block:

```nginx
# Must match Django's 10MB upload cap — without it, big CSVs 413 at nginx first
client_max_body_size 10m;

# Dashboard JSON is 50–200 KB; gzips to ~10–30 KB
gzip on;
gzip_vary on;
gzip_min_length 1024;
gzip_types text/plain text/css text/javascript application/json application/javascript;

# Hashed static assets are immutable — cache forever
location /static/ {
    alias /home/bibliotype/app/staticfiles/;
    expires 1y;
    add_header Cache-Control "public, immutable";
    access_log off;
}
```

And above the `server` block (http context — put it in `/etc/nginx/conf.d/ratelimit.conf`):

```nginx
limit_req_zone $binary_remote_addr zone=app:10m rate=15r/s;
```

then inside the existing `location / { ... proxy_pass ... }`:

```nginx
limit_req zone=app burst=30 nodelay;
```

Apply: `sudo nginx -t && sudo systemctl reload nginx`

## 2. Docker log rotation (~5 min)

Docker json-file logs grow unbounded. `/etc/docker/daemon.json`:

```json
{
    "log-driver": "json-file",
    "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

```bash
sudo systemctl restart docker
cd /home/bibliotype/app && docker compose -f docker-compose.prod.yml up -d --force-recreate
```

(Only takes effect for recreated containers.)

## 3. Django log rotation (~2 min)

`/etc/logrotate.d/bibliotype`:

```
/home/bibliotype/app/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    copytruncate
}
```

## 4. Daily Postgres backup (~5 min)

```bash
mkdir -p ~/backups ~/scripts
cat > ~/scripts/backup-db.sh <<'EOF'
#!/bin/bash
set -e
cd /home/bibliotype/app
DATE=$(date +%Y%m%d)
docker compose -f docker-compose.prod.yml exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > ~/backups/bibliotype-$DATE.sql.gz
find ~/backups -name 'bibliotype-*.sql.gz' -mtime +14 -delete
EOF
chmod +x ~/scripts/backup-db.sh
~/scripts/backup-db.sh && ls -lh ~/backups   # test it works
(crontab -l 2>/dev/null; echo "0 4 * * * ~/scripts/backup-db.sh") | crontab -
```

Periodically copy a backup off the droplet (laptop or object storage) — a
droplet-local backup doesn't survive the droplet dying.

## 5. Uptime monitoring (~5 min, free)

[Uptime Robot](https://uptimerobot.com) free tier:

- Monitor 1: `https://<domain>/` — 5-minute interval, email alert
- Monitor 2: `https://<domain>/admin/login/` — catches DB-down (admin needs the DB)

## 6. Verification

```bash
free -h                                   # 2GB swap present
docker stats --no-stream                  # db ≤280m, redis ≤130m
sudo nginx -T | grep -E 'client_max_body_size|gzip on|limit_req'
docker compose -f docker-compose.prod.yml exec redis redis-cli config get maxmemory-policy   # volatile-lru
docker compose -f docker-compose.prod.yml exec web ps aux | grep gunicorn    # --max-requests visible
curl -sI -H 'Accept-Encoding: gzip' https://<domain>/ | grep -i content-encoding   # gzip
crontab -l                                # backup cron present
```
