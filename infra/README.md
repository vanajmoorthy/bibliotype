# Bibliotype infrastructure (Terraform)

Provisions the production droplet on DigitalOcean: droplet + reserved IP + cloud
firewall (22/80/443 only) + cloud-init bootstrap (Docker, 2GB swap, `bibliotype`
deploy user, nginx, certbot, SSH hardening). App deployment is NOT managed here —
that stays with the GitHub Actions SSH deploy (`.github/workflows/deploy.yml`).

Background and rationale: `docs/infra/terraform-migration-plan.md`.

## First-time setup

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # fill in region, key names, deploy pubkey
export DIGITALOCEAN_TOKEN=...                   # DO API token (write scope)
terraform init
terraform plan
terraform apply
```

State is local (`terraform.tfstate`, gitignored). Fine for a single operator; move
to Terraform Cloud (free) or a DO Spaces backend before anyone else runs this.

## Migration runbook (old box → new box)

1. `terraform apply` → note the `reserved_ip` output. Wait ~3–5 min for cloud-init
   (`ssh bibliotype@<droplet_ip>`, then `cloud-init status --wait`, `docker --version`,
   `swapon --show`).
2. Copy the prod env file: `scp` `/home/bibliotype/app/.env` from the old box to the
   same path on the new one. Review it while you're there (GEMINI_MODEL etc.).
3. Copy the nginx site config + certs from the old box (`/etc/nginx/sites-available/`,
   then `certbot --nginx` on the new box to reissue rather than copying certs).
4. Update the `DO_SSH_HOST` GitHub secret to the **reserved IP** (one-time — future
   rebuilds keep it). `DO_SSH_USERNAME` stays `bibliotype`; `DO_SSH_KEY` unchanged.
5. Trigger the deploy workflow (push to main or re-run the last one) → stack comes up.
6. Migrate data — from the old box:
   ```bash
   docker exec app-db-1 pg_dump -U $POSTGRES_USER -Fc $POSTGRES_DB > bibliotype.dump
   ```
   copy across, then on the new box:
   ```bash
   docker exec -i app-db-1 pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists < bibliotype.dump
   ```
   Sanity-check: row counts, log in, load a dashboard.
7. Cutover: point DNS A record at the reserved IP (or if DNS already targets it,
   nothing to do). Old box keeps running as fallback.
8. After a quiet day or two: snapshot the old droplet, then destroy it.

## Launch-week resize

```bash
# terraform.tfvars: droplet_size = "s-2vcpu-2gb"    # $18
terraform apply     # brief power-off; disk untouched (resize_disk = false)
# after launch: revert the variable, apply again → back to $6
```

On 2 vCPU, Celery `-c 2` becomes viable — separate compose change, see the
queue-split plan.

## Post-provision hardening (after first successful SSH as bibliotype)

Flip `PermitRootLogin prohibit-password` → `no` in
`/etc/ssh/sshd_config.d/99-hardening.conf` and `systemctl restart ssh`.
