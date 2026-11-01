# [5.3] #19. Everything the production deploy runs on, on DigitalOcean.
# docs/deployment.md is the walkthrough; ADR 0026 is why it looks like this.
#
# State is kept in terraform.tfstate in this directory, on the machine that
# ran `apply`. It holds every generated secret, so it is gitignored and must
# not be shared anywhere public.

terraform {
  required_version = ">= 1.6"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

# Reads the API token from DIGITALOCEAN_TOKEN, so it never sits in a file.
provider "digitalocean" {}
