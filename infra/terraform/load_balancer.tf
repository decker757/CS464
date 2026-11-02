locals {
  # The frontend on the domain itself, and one subdomain per service.
  # deploy/production/gateway/default.conf.template routes these names.
  service_subdomains = ["auth", "market", "ledger", "audit", "realtime"]
  site_names         = concat([var.domain], [for name in local.service_subdomains : "${name}.${var.domain}"])
}

# A Let's Encrypt certificate that DigitalOcean issues and renews by itself,
# which it can do because it also runs the domain's DNS (dns.tf).
resource "digitalocean_certificate" "cs464" {
  name    = "cs464-${replace(var.domain, ".", "-")}"
  type    = "lets_encrypt"
  domains = local.site_names

  # Issued only once DigitalOcean runs the domain's DNS.
  depends_on = [digitalocean_domain.cs464]

  lifecycle {
    create_before_destroy = true
  }
}

# Ends TLS and spreads requests across every server tagged cs464-app, using
# only the ones whose /lb-health answers 200. roll.sh relies on these
# timings to drain a server: see its drain_seconds.
resource "digitalocean_loadbalancer" "cs464" {
  name        = "cs464"
  region      = var.region
  vpc_uuid    = digitalocean_vpc.cs464.id
  droplet_tag = digitalocean_tag.app.id

  forwarding_rule {
    entry_protocol   = "https"
    entry_port       = 443
    target_protocol  = "http"
    target_port      = 80
    certificate_name = digitalocean_certificate.cs464.name
  }

  forwarding_rule {
    entry_protocol  = "http"
    entry_port      = 80
    target_protocol = "http"
    target_port     = 80
  }

  redirect_http_to_https = true

  healthcheck {
    protocol                 = "http"
    port                     = 80
    path                     = "/lb-health"
    check_interval_seconds   = 3
    response_timeout_seconds = 3
    unhealthy_threshold      = 3
    healthy_threshold        = 2
  }

  # The longest a quiet connection stays open. Price websockets can sit idle
  # between trades; the client reconnects after this anyway (#37).
  http_idle_timeout_seconds = 600
}
