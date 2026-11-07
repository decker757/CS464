variable "domain" {
  description = "The site's domain, e.g. predictsmu.xyz. Its nameservers must point at DigitalOcean (ns1-3.digitalocean.com)."
  type        = string
}

variable "region" {
  description = "Where everything runs. Singapore, near the people using it."
  type        = string
  default     = "sgp1"
}

variable "app_server_count" {
  description = "How many app servers run behind the load balancer. Every server shares the four connection pools in databases.tf, so a third fits the database (docs/deployment.md, \"Connection budget\")."
  type        = number
  default     = 2

  validation {
    condition     = var.app_server_count >= 1
    error_message = "At least one app server."
  }
}

variable "app_server_size" {
  description = "Each app server's plan: 1 vCPU and 2 GB runs the five services and the gateway."
  type        = string
  default     = "s-1vcpu-2gb"
}

variable "database_size" {
  description = "The managed Postgres plan. 2 GB, for its connection limit rather than its memory."
  type        = string
  default     = "db-s-1vcpu-2gb"
}

variable "cache_size" {
  description = "The managed Valkey (Redis-compatible) plan that carries live prices."
  type        = string
  default     = "db-s-1vcpu-1gb"
}
