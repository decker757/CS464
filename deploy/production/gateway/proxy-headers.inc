# [5.3] #19. Included by every service's location in default.conf.template.
# The load balancer ended TLS, so its protocol header is passed on and the
# services build https URLs. The client's address is not passed on as it
# arrived: default.conf.template resolves the real client, and this hands the
# services that one address. uvicorn trusts the leftmost X-Forwarded-For
# entry, which a visitor can write, so appending to the header would let a
# visitor choose the address the services see. [BE][A-2] #600.
proxy_set_header Host $host;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
proxy_set_header X-Real-IP $remote_addr;
