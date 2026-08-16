output "droplet_ip" {
  description = "Droplet's own public IP (changes if the droplet is replaced)."
  value       = digitalocean_droplet.bibliotype.ipv4_address
}

output "reserved_ip" {
  description = "Stable public IP — point DNS and the DO_SSH_HOST GitHub secret at this."
  value       = digitalocean_reserved_ip.bibliotype.ip_address
}
