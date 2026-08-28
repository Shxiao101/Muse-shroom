# Muse-shroom blind A/B evaluation

This evaluation checks whether a new version produces more useful inspiration without treating any repository as a canonical answer.

1. Freeze one GitHub response set per prompt in `ab-prompts.json`; both versions must consume the same repository metadata and README snapshots.
2. Run the v0.2 baseline and v0.3 candidate against those snapshots. Remove version labels and randomize whether each list is shown as A or B.
3. For every prompt, rate both lists from 1 to 5 on `relevance`, `interesting`, `evidence`, `actionability`, and `diversity`, then choose `baseline`, `candidate`, or `tie` as the overall preference.
4. Save ratings using `ratings.example.json` and run `python evaluation/score_ab.py RATINGS.json`.

The release gate passes when the candidate wins at least 60% of all prompts, its median evidence score improves by at least 0.5, and median relevance and diversity do not decrease. Probe repositories may be recorded separately but never affect this calculation.
