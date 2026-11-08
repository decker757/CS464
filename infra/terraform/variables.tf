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
  description = "How many app servers run behind the load balancer. Every server shares the four connection pools in databases.tf, and the ledger's pool of 24 holds 6 + 2 ledger clients on each of up to three (docs/deployment.md, \"Connection budget\")."
  type        = number
  default     = 2

  validation {
    condition     = var.app_server_count >= 1
    error_message = "At least one app server."
  }

  # A fourth server's ledger clients (4 x 8 = 32) would outnumber the ledger's
  # pool of 24 and bring #667 back. Resize the pool or the client pools first.
  validation {
    condition     = var.app_server_count <= 3
    error_message = "At most three app servers: the ledger's pool of 24 holds three servers' 6 + 2 ledger clients. Resize it first (docs/deployment.md, \"The ledger's pool holds all of its clients\")."
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
