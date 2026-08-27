---
name: github-inspiration-discovery
description: Discover relevant GitHub repositories, including popular representatives, hidden gems, and adjacent inspiration, when a user describes a fuzzy interest, problem, tool, plugin, mod, MCP, or agent skill. Use Muse-shroom for evidence-backed retrieval; do not use it for known-repository code search or automatic installation.
---

# GitHub Inspiration Discovery

Turn a fuzzy request into a diverse, evidence-backed shortlist with Muse-shroom. Keep natural-language interpretation here; leave GitHub query syntax, caching, relationship expansion, and deterministic ranking to the CLI.

## Choose a mode

- Use **quick** when the user wants a lightweight shortlist. Perform one `search` and one `rank`.
- Use **deep** when the user names a niche concept, wants hidden gems, or asks whether a specific kind of surprising repository can be rediscovered. Perform `search`, inspect the first-round vocabulary and evidence, then `expand`, then `rank`.

Before invoking the CLI, read [request-contract.md](references/request-contract.md). Before assessing candidates, read [assessment-contract.md](references/assessment-contract.md).

## Interpret the request

Separate the surface phrase from the underlying symptom. For example, “Codex overthinks” can mean latency, token cost, over-design, repeated review, or excessive caution. Preserve these as distinct concepts so a repository about implementation minimalism is not presented as a latency fix.

Include:

- core concepts that must match;
- adjacent concepts that create useful surprise;
- likely artifact types;
- constraints and explicit exclusions;
- an exploration level reflecting how far the user wants to roam.

Do not write GitHub qualifiers yourself. Supply plain concepts through the request contract.

## Retrieve and refine

Invoke `muse-shroom search --request REQUEST --mode quick|deep` and retain its `search_id`.

For deep mode, examine descriptions, Topics, discovery paths, and README evidence from the first round. Add only terminology supported by that evidence: domain terms, aliases, project anchors, characteristic filenames, and exclusions. Invoke `muse-shroom expand --search-id ID --refinement REFINEMENT` once. Do not inject a repository name the user asked the workflow to rediscover blindly.

If output is stale or has `incomplete_phase`, disclose it. Do not silently treat cached or partial coverage as current and complete.

## Assess and rank

Assess useful candidates with the fixed contract. Every reason and risk must cite evidence IDs present on that candidate. Mark unverified capabilities as unknown; repository names and Star counts are not feature evidence.

Apply type-aware judgment:

- applications: installation, usable entry point, releases;
- MCP servers: tool contract and permission scope;
- Skills: trigger boundary and instruction scope;
- Mods: compatible versions, uninstall path, and conflicts.

Invoke `muse-shroom rank --search-id ID --assessments -`. Present the returned popular, gems, and adjacent buckets without filling missing slots. Explain the discovery path for surprising recommendations and distinguish a demonstrated relationship from an inference.

## Boundaries

Muse-shroom only reads public GitHub data. Never clone, execute, install, or grant permissions to a candidate unless the user separately requests and authorizes that work. Never expose `GITHUB_TOKEN` in prompts or output.
