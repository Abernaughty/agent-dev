"""Chroma-backed tiered memory store.

Implements L0-Core, L0-Discovered, L1, L2 memory tiers
using Chroma with metadata tagging.

Designed to be database-agnostic for future pgvector migration.

Implementation in Step 2.
"""

# TODO Step 2:
# - Initialize Chroma client (persistent storage in chroma_data/)
# - Define collection with metadata schema (tier, verified, expires_at, module)
# - Implement write helpers: add_l0_core(), add_l0_discovered(), add_l1(), add_l2()
# - Implement read helpers: query_context(module, tiers)
# - Implement cleanup: expire_l2(), expire_unverified_l0()
# - Seed L0-Core with reference stack from plan
