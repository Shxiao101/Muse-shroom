# Duplicate-query diagnosis

`query_fingerprint` lowercases and sorts whitespace-delimited query tokens. It therefore
recognizes a repeated GitHub query even when qualifier order or letter case changes. It
also deliberately loses token order inside quoted phrases: `"alpha beta"` and
`"beta alpha"` receive the same fingerprint. That is a real over-match at the utility
boundary, although no such reordered-phrase collision was observed in development
attempt 11.

The attempt-11 stops were ordinary duplicates, not fingerprint collisions. In each
affected case the Agent selected an already-searched request mechanism or exploration
direction as the next base query (for example, `"knowledge workflow"`), producing the
same term and qualifiers as the initial search. The semantic sidecar still executed its
separate hypothesis queries, but the former base-only `skipped_all` rule converted the
duplicate into a hard stop.

Duplicate fingerprints remain useful for avoiding repeated API calls. They are now an
advisory `duplicate_queries` signal only. Session termination remains bounded by
`max_iterations`, the base query budget, an explicit Agent stop, and the existing
consecutive-no-gain limit.
