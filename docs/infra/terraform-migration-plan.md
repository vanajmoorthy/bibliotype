# Terraform migration plan: fresh droplet, clean rebuild

**Status:** parked — PR intentionally left open until we're ready to execute.
**Committed under `docs/infra/`** (not `docs/plans/`, which is gitignored) because this
plan is meant to be reviewed and kept in repo history.

## Goal

Replace the current 687-day-uptime droplet (94% disk, swap-full, Plex + stale preview
container + publicly-bound MariaDB accumulated on it) with a **fresh $6 droplet**
(s-1vcpu-1gb) provisioned entirely by Terraform + cloud-init, so that:

- The rebuild is reproducible — no hand-accumulated clutter this time.
- **Scaling to $18 (s-2vcpu-2gb) at launch is a one-line variable change**, and can be
  resized back down after launch week (CPU/RAM-only resize is reversible; we never
  grow the disk, so the flexible resize path stays open).
- The security holes (MariaDB on 0.0.0.0:3306, Plex on 32400) are structurally closed
  by a DO cloud firewall rather than remembered-about.

Tooling: **Terraform (or OpenTofu — interchangeable here) + the `digitalocean`
provider.** SST was evaluated and rejected (AWS/Cloudflare-first, no droplet story).
IaC owns "the box exists and is configured"; **app deployment stays with the existing
GitHub Actions SSH deploy — no creep.**

## Repo layout

```
infra/
├── main.tf          # provider, droplet, reserved IP, firewall, DNS
├── variables.tf     # droplet_size (default "s-1vcpu-1gb"), region, ssh key ids
├── outputs.tf       # droplet IP, reserved IP
├── cloud-init.yaml  # bootstrap: docker, swap, deploy user, hardening
└── README.md        # apply/resize/destroy runbook
```

## Resources (~100–150 lines total)

1. **`digitalocean_droplet`** — Ubuntu 24.04 LTS, `size = var.droplet_size`
   (default `s-1vcpu-1gb`, $6), `region` matching the current droplet, ssh_keys,
   `user_data = file("cloud-init.yaml")`.
   - Resize procedure: set `droplet_size = "s-2vcpu-2gb"` ($18), `terraform apply`
     (droplet powers off briefly). **`resize_disk = false`** so the resize stays
     reversible — we can go back to $6 after launch.

2. **`digitalocean_reserved_ip`** (+ assignment) — the public IP survives droplet
   replacement, so GitHub Actions deploy secrets and DNS never need to change when
   we rebuild or resize-by-replace.

3. **`digitalocean_firewall`** — inbound: 22 (SSH), 80, 443. **Nothing else.**
   No 3306, no 32400. Outbound: all. This is the structural fix for the current
   exposure — even if a future service binds 0.0.0.0, it isn't reachable.

4. **`digitalocean_domain` / `digitalocean_record`** — A record(s) for the
   bibliotype domain → reserved IP. (Only if DNS is on DO; if it's elsewhere,
   skip and note the manual A-record flip in the runbook.)

## cloud-init.yaml

- Install Docker Engine + compose plugin (official apt repo).
- **2GB swapfile** (`/swapfile`, `vm.swappiness=10`, fstab entry) — OOM insurance
  for the pandas peak; on the fresh 1GB box this is created before anything can
  fragment the disk.
- `deploy` user with the SSH public key GitHub Actions uses; docker group.
- SSH hardening: `PasswordAuthentication no`, `PermitRootLogin no`.
- `unattended-upgrades` for security patches (the old box ran a 2024 kernel).
- Create `/opt/app` (or match the current deploy path — check the Actions workflow)
  owned by `deploy`.

## State backend

Start with **Terraform Cloud free tier** (zero cost, remote state, no secrets in the
repo). Alternative: DO Spaces S3 backend (~$5/mo, doubles as a backup bucket).
Never commit state files — they can contain the DO token and droplet details.
DO API token supplied via `DIGITALOCEAN_TOKEN` env var, never in `.tf` files.

## What moves, what doesn't

| Thing | Decision |
|---|---|
| Bibliotype stack (compose: db, redis, web, worker) | Moves — deployed by the existing GitHub Action onto the new box |
| Prod `.env` | Hand-copied over SSH once (it lives outside the repo by design) |
| Postgres data | `pg_dump` on old box → `pg_restore` on new (small DB; minutes) |
| puzzleflix (2 containers + host MariaDB) | Decide at migration time: bring over as compose services with MariaDB **bound to a private docker network** (never the host), or retire. Its DB needs a dump/restore if kept. |
| mcow.ml (PM2 + Mongo URI) | Self-described inactive. Recommend: retire, or move to a free Atlas cluster + one container if the demo matters. Do NOT install PM2 on the new box — containers only. |
| Plex | **Does not move.** Home box or nothing. |
| preview-pr-72 and friends | Die with the old droplet. |

## Migration runbook (once we execute)

1. `terraform apply` → fresh $6 droplet boots, cloud-init runs (verify: `docker --version`, `swapon --show`, firewall active in DO console).
2. Copy prod `.env` to the new box; point the GitHub Actions deploy secret at the
   reserved IP (or it's already the reserved IP — then nothing changes).
3. Run the deploy Action → bibliotype stack comes up on the new box.
4. `pg_dump` old → `pg_restore` new; sanity-check row counts + a login.
5. Assign the reserved IP to the new droplet (atomic cutover) — or flip DNS A record
   if DNS is external. Old box keeps running as fallback.
6. Watch logs/PostHog for a day or two.
7. Snapshot the old droplet (cheap insurance), then destroy it. Costs overlap ~$6
   for the transition week; that's the whole migration budget.

## Launch-week resize

```
# variables.tf: droplet_size = "s-2vcpu-2gb"   # $18
terraform apply    # brief power-off resize, disk untouched
# after launch settles, revert the variable and apply again → back to $6
```

Pair the resize with the compose-side scaling knobs (Gunicorn threads are already in
place from the earlier PR; on 2 vCPU, Celery `-c 2` becomes viable — separate
decision, noted in the queue-split plan).

## Out of scope

- Kubernetes, Kamal, Coolify, Pulumi — rejected; overhead exceeds the entire box.
- Managed Postgres/Redis — revisit only if the app outgrows the single box.
- CI changes — the deploy workflow is untouched except possibly the host IP secret.
