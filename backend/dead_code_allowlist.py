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

# [3.4] #12 PR 5: the trade latch and settlement read it; delete this line there
MarketResult

# [3.4] #12 PR 5: settlement posts through it; delete this line there
post_all

# [3.4] #12 PR 5: settlement calls it after the payout commits; delete this line there
mark_settled
