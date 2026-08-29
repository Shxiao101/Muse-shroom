# Search request contract

Pass a JSON object to `muse-shroom search --request FILE --mode quick|deep`.

Iterate hypotheses use [hypothesis-contract.md](hypothesis-contract.md). Assessments use [assessment-contract.md](assessment-contract.md). Rank output uses [result-contract.md](result-contract.md).

```json
{
  "request": "the user's original request",
  "problem_concepts": [
    {"term": "problem to solve", "aliases": ["github-common alias"], "weight": 1.0}
  ],
  "mechanisms": [
    {"term": "concrete solution mechanism", "aliases": ["mechanism alias"], "weight": 0.8}
  ],
  "exploration_directions": [{"term": "not-yet-covered direction", "weight": 0.6}],
  "artifact_types": ["application", "mcp", "skill", "mod", "plugin", "library"],
  "constraints": {
    "language": "Python",
    "pushed_after": "2025-01-01",
    "include_archived": false
  },
  "exclusions": ["course", "awesome list"],
  "exploration_level": 0.5
}
```

`request` and at least one `problem_concepts` entry are required. Weights and `exploration_level` range from 0 to 1. Omit constraints the user did not state; do not invent minimum Star counts because low exposure is part of hidden-gem discovery.

`term` is the concept the user understands. Terms and aliases are single-line strings up to 160 characters. `aliases` are GitHub-common expressions, English terms, or domain words for the same concept; at most four per concept. Aliases in one group count as one concept and must not be used to stack scores. Keep generic artifact words such as `skill`, `tool`, `AI`, and `agent` out of problem/mechanism concepts and aliases when a specific term is available. Put the desired form in `artifact_types`. The CLI ignores standalone generic problem terms and will not emit an isolated `"Skill"` query.

Preserve concise Chinese capability phrases verbatim, for example `正文配图`, `文章配图`, `专注管理`, and `自控训练`. Keep English concepts to one to three meaningful words. For a non-English need, add at least one GitHub-common English expression as an alias. Do not use a fixed translation dictionary.

The CLI still accepts v0.3 `core_concepts` / `adjacent_concepts` and converts them. New Agent requests must use the v0.4 fields above. Muse-shroom generates GitHub syntax; do not write qualifiers yourself.
