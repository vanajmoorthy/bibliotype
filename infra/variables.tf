variable "do_token" {
  description = "DO API token (write scope). Prefer the DIGITALOCEAN_TOKEN env var; set here (terraform.tfvars, gitignored) only for convenience."
  type        = string
  sensitive   = true
  default     = null
}

variable "project_name" {
  description = "Existing DO project to file the droplet + reserved IP under (e.g. \"bibliotype\"). Empty = default project."
  type        = string
  default     = ""
}

variable "droplet_size" {
  description = "Droplet slug. $6 = s-1vcpu-1gb. Launch week: s-2vcpu-2gb ($18), then revert. CPU/RAM resizes are reversible because main.tf sets resize_disk = false."
  type        = string
  default     = "s-1vcpu-1gb"
}

variable "region" {
  description = "DO region slug. Match the current droplet's region (DO console, or `doctl compute droplet list --format Name,Region`) to keep latency to existing users unchanged."
  type        = string
}

variable "personal_ssh_public_key" {
  description = "Your laptop's public key (contents of ~/.ssh/id_ed25519_do.pub). Terraform uploads it to the DO account; DO puts it on root, cloud-init also authorizes it for the bibliotype user."
  type        = string
}

variable "deploy_ssh_public_key" {
  description = "Public key counterpart of the DO_SSH_KEY GitHub Actions secret. Authorized for the bibliotype deploy user so the deploy workflow can SSH in."
  type        = string
}

variable "repo_url" {
  description = "Repo cloned to /home/bibliotype/app (the deploy workflow runs git fetch/reset there)."
  type        = string
  default     = "https://github.com/vanajmoorthy/bibliotype.git"
}

variable "manage_dns" {
  description = "Create the DO domain + A records. Only if the domain's nameservers are DigitalOcean's; otherwise flip the A record at your DNS host during cutover."
  type        = bool
  default     = false
}

variable "domain" {
  description = "Apex domain for the site (used only when manage_dns = true)."
  type        = string
  default     = ""
}
