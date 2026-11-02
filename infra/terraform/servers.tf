# The app servers. Each runs deploy/production/compose.yml, the same on every
# one, and holds no data, so any of them can be replaced.

resource "digitalocean_droplet" "app" {
  count    = var.app_server_count
  name     = "cs464-app-${count.index + 1}"
  region   = var.region
  size     = var.app_server_size
  image    = "ubuntu-24-04-x64"
  vpc_uuid = digitalocean_vpc.cs464.id
  tags     = [digitalocean_tag.app.id]

  # DigitalOcean's free CPU, memory and disk graphs.
  monitoring = true

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    deploy_public_key = tls_private_key.deploy.public_key_openssh
    host_private_key  = tls_private_key.host[count.index].private_key_openssh
    host_public_key   = tls_private_key.host[count.index].public_key_openssh
  })

  lifecycle {
    # cloud-init runs once, on first boot. Changing it later would rebuild a
    # live server; do that on purpose, with `terraform apply -replace`.
    ignore_changes = [user_data]
  }
}

# Only the load balancer may reach the gateway. SSH is open to the internet
# because GitHub's runners have no fixed address; it takes keys only.
resource "digitalocean_firewall" "app" {
  name = "cs464-app"
  tags = [digitalocean_tag.app.id]

  inbound_rule {
    protocol                  = "tcp"
    port_range                = "80"
    source_load_balancer_uids = [digitalocean_loadbalancer.cs464.id]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
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
