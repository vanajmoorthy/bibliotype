terraform {
  required_version = ">= 1.5"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
  }

  # Local state to start (single operator). Move to Terraform Cloud free tier or a
  # DO Spaces S3 backend before a second operator or CI ever runs this.
  # State files are gitignored — they can contain sensitive values.
}

# Auth: export DIGITALOCEAN_TOKEN=... (never put the token in .tf/.tfvars files)
provider "digitalocean" {}

# Existing SSH keys already uploaded to the DO account (personal + deploy).
data "digitalocean_ssh_key" "keys" {
  for_each = toset(var.ssh_key_names)
  name     = each.value
}

resource "digitalocean_droplet" "bibliotype" {
  name       = "bibliotype-prod"
  image      = "ubuntu-24-04-x64"
  size       = var.droplet_size
  region     = var.region
  monitoring = true
  ssh_keys   = [for k in data.digitalocean_ssh_key.keys : k.id]

  # CPU/RAM-only resizes (resize_disk = false) are reversible: $6 -> $18 for
  # launch week, then back. Growing the disk would be one-way.
  resize_disk = false

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    deploy_ssh_public_key = var.deploy_ssh_public_key
    repo_url              = var.repo_url
  })
}

# The public IP survives droplet replacement/rebuilds, so DNS records and the
# DO_SSH_HOST GitHub Actions secret never need to change again.
resource "digitalocean_reserved_ip" "bibliotype" {
  region = var.region
}

resource "digitalocean_reserved_ip_assignment" "bibliotype" {
  ip_address = digitalocean_reserved_ip.bibliotype.ip_address
  droplet_id = digitalocean_droplet.bibliotype.id
}

# Structural fix for the old box's exposure (MariaDB on 0.0.0.0:3306, Plex on
# 32400): only 22/80/443 are reachable, regardless of what binds inside.
resource "digitalocean_firewall" "bibliotype" {
  name        = "bibliotype-prod"
  droplet_ids = [digitalocean_droplet.bibliotype.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

# DNS — only if the domain's nameservers point at DigitalOcean. If DNS lives at
# the registrar/Cloudflare, leave manage_dns = false and flip the A record there
# during cutover.
resource "digitalocean_domain" "main" {
  count = var.manage_dns ? 1 : 0
  name  = var.domain
}

resource "digitalocean_record" "apex" {
  count  = var.manage_dns ? 1 : 0
  domain = digitalocean_domain.main[0].id
  type   = "A"
  name   = "@"
  value  = digitalocean_reserved_ip.bibliotype.ip_address
  ttl    = 300
}

resource "digitalocean_record" "www" {
  count  = var.manage_dns ? 1 : 0
  domain = digitalocean_domain.main[0].id
  type   = "A"
  name   = "www"
  value  = digitalocean_reserved_ip.bibliotype.ip_address
  ttl    = 300
}
