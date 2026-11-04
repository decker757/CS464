# [5.5] #599. Included by every service's server block in
# default.conf.template. Paths that only the private network may reach.
# /ready names which dependency is down, which is for Alloy, scraping over the
# private network, and not for the internet. Add a path here to hide it.
# ADR 0028.
location = /ready { return 404; }
