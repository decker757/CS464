# DigitalOcean runs the domain's DNS, so it can issue the certificate. Every
# name points at the load balancer.
resource "digitalocean_domain" "cs464" {
  name = var.domain
}

resource "digitalocean_record" "site" {
  for_each = toset(concat(["@"], local.service_subdomains))
  domain   = digitalocean_domain.cs464.id
  type     = "A"
  name     = each.value
  value    = digitalocean_loadbalancer.cs464.ip
  ttl      = 300
}
