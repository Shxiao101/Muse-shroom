# Muse-shroom blind A/B evaluation

This evaluation checks whether a new version produces a more useful assessment shortlist without treating any repository as a canonical answer. It compares the v0.2.0 baseline at `5cc5621` with the current worktree by default.

## Capture once

Run capture in the host user context so Muse-shroom can read the system credential store and reach GitHub:

```console
python evaluation/run_ab.py capture
```

The recorder runs both versions in isolated source trees. Calls with the same method and arguments reuse exactly the same recorded response; calls unique to one version are captured during the same session. Repository search is paced below GitHub's 30 requests/minute search limit. Authentication failures, rate limits, and transient errors stop capture instead of becoming frozen fixtures.

Generated cassettes and results are deliberately ignored by Git because they contain large snapshots of external README content. The default outputs are:

```text
evaluation/cassettes/ab-v1.json.gz
evaluation/results/baseline.raw.json
evaluation/results/candidate.raw.json
evaluation/results/blind-review.json
evaluation/results/blind-key.json
```

## Replay and review

Replay performs no network calls and fails explicitly if a required API call is absent:

```console
python evaluation/run_ab.py replay
```

Open only `blind-review.json` while rating. For every prompt, rate lists A and B from 1 to 5 on `relevance`, `interesting`, `evidence`, `actionability`, and `diversity`, then choose `A`, `B`, or `tie`. Save ratings using `ratings.example.json`; do not inspect `blind-key.json` until ratings are final.

```console
python evaluation/score_ab.py RATINGS.json --key evaluation/results/blind-key.json --output evaluation/results/summary.json
```

The release gate passes when all eight prompts are rated, the candidate wins at least 60%, its median evidence score improves by at least 0.5, and median relevance and diversity do not decrease. Probe repositories may be recorded separately but never affect this calculation.

This v1 harness evaluates the 24-repository assessment shortlist and its evidence, which is the stage changed most heavily in v0.3. Final Agent-authored semantic assessments and ranking prose remain a separate human-in-the-loop evaluation.

The assessment shortlist caps a single repository owner at three entries. This keeps ecosystems such as a release tool plus its plugins discoverable without allowing one owner to dominate the Agent's review budget.
