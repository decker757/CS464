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


# Alembic calls these by name in every migrations/versions/*.py; nothing
# imports them. [F-5] #75.
upgrade
downgrade
