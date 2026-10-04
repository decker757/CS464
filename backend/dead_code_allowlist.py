"""Code that nothing in production calls, kept on purpose. [F-11] #198.

`check_dead_code.sh` hands this file to vulture as code, so naming something
here counts as using it. Every entry says why it stays. Delete the entry when
its reason goes. This file is never imported or run.
"""

# Test seams. The suite drops the process-wide hub and gate between tests;
# production never does, since it would orphan every open socket.
reset_gate
reset_hub

# Test introspection. The suite asserts that a closed socket leaves no
# subscription behind.
subscriber_count
connection_count

# No production caller since [2.1] #5 built its own overview. Whether to
# delete it and supersede D-020 is being decided on #194.
count_by_status


# Used by #104 Task 4 (the paged browse). Temporary; Task 4 removes these.
encode_browse_cursor
decode_browse_cursor
MarketPage
