# Search request contract

Pass a JSON object to `muse-shroom search --request FILE --mode quick|deep`.

```json
{
  "request": "the user's original request",
  "core_concepts": [{"term": "required concept", "weight": 1.0}],
  "adjacent_concepts": [{"term": "nearby concept", "weight": 0.6}],
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

`request` and at least one `core_concepts` entry are required. Weights and `exploration_level` range from 0 to 1. Omit constraints the user did not state; do not invent minimum Star counts because low exposure is part of hidden-gem discovery.

Keep generic artifact words such as `skill`, `tool`, `AI`, and `agent` out of `core_concepts` when a problem or domain term is available. Put the desired form in `artifact_types`. The CLI ignores standalone generic core terms and will not emit an isolated `"Skill"` query.

Preserve concise Chinese concepts verbatim, for example `正文配图` and `文章配图`; do not split them by character, whitespace, or Latin-word rules. Keep English concepts to one to three meaningful words.

For deep refinement, pass:

```json
{
  "concepts": ["domain term or alias"],
  "adjacent_concepts": ["supported nearby direction"],
  "anchors": ["term observed in first-round evidence"],
  "seeds": ["owner/repo selected from first-round candidates"],
  "filenames": ["distinctive filename observed in first-round evidence"],
  "exclude": ["newly observed irrelevant meaning"]
}
```

Keep all arrays short and evidence-driven. Muse-shroom generates and validates GitHub syntax.

All refinement values must be arrays of strings. Seeds use `owner/repo`; filenames must be basenames such as `SKILL.md`, never paths or query fragments. Original search constraints remain active during expansion.
