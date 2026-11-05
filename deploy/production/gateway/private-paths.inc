# [5.5] #599. Included by every service's server block in
# default.conf.template. Paths that only the private network may reach.
# /ready names which dependency is down, and /metrics lists every counter and
# gauge the service keeps. Both are for Alloy, scraping over the private
# network, and not for the internet. Add a path here to hide it.
# ADR 0028.
# The access_log line is proxy-headers.inc's: without it these locations fall
# back to the image's `combined` format, which logs the query string.
location = /ready {
    access_log /var/log/nginx/access.log cs464;
    return 404;
}
location = /metrics {
    access_log /var/log/nginx/access.log cs464;
    return 404;
}
# #640, ADR 0030. Every service-to-service route: auth's and market's lists
# that the ledger's local copies refresh from, and auth's lookup (ADR 0021)
# too. They need LEDGER_SERVICE_KEY, and the internet should not get to try
# one. `^~`, so that a regex location added later cannot take the path first.
location ^~ /internal/ {
    access_log /var/log/nginx/access.log cs464;
    return 404;
}
