# [5.5] #599. Included by every service's server block in
# default.conf.template. Paths that only the private network may reach.
# /ready names which dependency is down, which is for Alloy, scraping over the
# private network, and not for the internet. Add a path here to hide it.
# ADR 0028.
# The access_log line is proxy-headers.inc's: without it this location falls
# back to the image's `combined` format, which logs the query string.
location = /ready {
    access_log /var/log/nginx/access.log cs464;
    return 404;
}
