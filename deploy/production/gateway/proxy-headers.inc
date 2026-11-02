# [5.3] #19. Included by every service's location in default.conf.template.
# The load balancer ended TLS and recorded the client, so its headers are
# passed on rather than replaced: the services then build https URLs, and see
# the client's address instead of the balancer's.
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
proxy_set_header X-Real-IP $remote_addr;
