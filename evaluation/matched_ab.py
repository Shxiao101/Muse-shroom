"""Matched-pair A/B tooling from `evaluation/ab-protocol.md`.

The direct arm, its adaptation, claim traceability, request loading, the run
schedule, and the matched blind pack belong to the registered real-need
experiment. They live here, not in `run_ab.py`, which captures and replays the
synthetic categorized-prompt flow.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evaluation.host_eval import _component_digest  # noqa: E402
from muse_shroom.github import GitHubClient, GitHubNotFoundError  # noqa: E402
from muse_shroom.storage import Store  # noqa: E402

REQUESTS_PATH = ROOT / "evaluation" / "ab-requests.json"
SCHEMA_VERSION = 2
COLLECTION_STATUS = "ready_for_evaluation"
# ab-protocol.md fixes the release threshold over exactly 8 needs (6 of 8 verdicts).
EXPECTED_REQUEST_COUNT = 8
REQUEST_FIELDS = frozenset({"id", "captured_at", "text"})
ARMS = ("muse-shroom", "direct")
# Recorded in the manifest so a capture can be reproduced or extended later.
DEFAULT_SEED = "muse-shroom-matched-v1"
METADATA_FIELDS = (
    "model_id", "muse_shroom_revision", "skill_component_digest",
    "timestamp", "configuration",
)


def load_requests(path: Path | None = None) -> list[dict[str, Any]]:
    """Load the registered verbatim needs, refusing anything past the protocol.

    The file must be schema v2, marked ready for evaluation, and hold exactly the
    registered need count. Entries may only carry id/captured_at/text: any extra
    field is pre-run interpretation injected into both arms, so it is rejected
    mechanically instead of trusting the operator to leave it out.
    """
    payload = json.loads((path or REQUESTS_PATH).read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"ab-requests.json schema_version must be {SCHEMA_VERSION}")
    if payload.get("collection_status") != COLLECTION_STATUS:
        raise ValueError(
            f'ab-requests.json collection_status must be "{COLLECTION_STATUS}"'
        )
    requests = payload.get("requests")
    if not isinstance(requests, list) or len(requests) != EXPECTED_REQUEST_COUNT:
        raise ValueError(
            f"ab-requests.json must hold exactly {EXPECTED_REQUEST_COUNT} requests"
        )
    needs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(requests):
        if not isinstance(entry, dict):
            raise ValueError(f"request {index} must be an object")
        if set(entry) != REQUEST_FIELDS:
            raise ValueError(
                f"request {entry.get('id') or index} fields must be exactly "
                f"id/captured_at/text; got {sorted(entry)}"
            )
        need_id = entry["id"]
        if not isinstance(need_id, str) or not need_id:
            raise ValueError(f"request {index} id must be a non-empty string")
        if need_id in seen:
            raise ValueError(f"request id {need_id} is duplicated")
        for field in ("captured_at", "text"):
            if not isinstance(entry[field], str) or not entry[field]:
                raise ValueError(f"request {need_id} {field} must be a non-empty string")
        seen.add(need_id)
        needs.append({"id": need_id, "captured_at": entry["captured_at"], "text": entry["text"]})
    return needs


def build_schedule(seed: str, reps: int, *, root: Path | None = None) -> dict[str, Any]:
    """Seed the run manifest: per repetition, every need meets both arms in a
    freshly shuffled order, so neither need order nor arm order is fixed.

    skill_component_digest is pinned at generation time, tying the schedule to the
    exact Skill and production source it was drawn against; the other metadata
    fields are per-run slots the operator fills in as each run executes.
    """
    if not isinstance(reps, int) or isinstance(reps, bool) or reps < 1:
        raise ValueError("reps must be a positive integer")
    needs = [need["id"] for need in load_requests()]
    rng = random.Random(seed)
    digest = _component_digest(root or ROOT)
    runs: list[dict[str, Any]] = []
    for repetition in range(1, reps + 1):
        pairs = [(need_id, arm) for need_id in needs for arm in ARMS]
        rng.shuffle(pairs)
        for position, (need_id, arm) in enumerate(pairs, 1):
            runs.append({
                "run_id": f"run-{repetition:02d}-{position:02d}",
                "need_id": need_id,
                "arm": arm,
                "repetition": repetition,
                "metadata": {
                    "model_id": None,
                    "muse_shroom_revision": None,
                    "skill_component_digest": digest,
                    "timestamp": None,
                    "configuration": None,
                },
            })
    return {"schema_version": 1, "seed": seed, "repetitions": reps, "runs": runs}


def adapt_direct_arm(
    requests_payload: dict[str, Any], direct_payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate direct-host output and emit the same result envelope as Muse-shroom."""
    requests = {
        str(item.get("id") or ""): item
        for item in requests_payload.get("requests") or []
    }
    direct = {
        str(item.get("prompt_id") or ""): item
        for item in direct_payload.get("results") or []
    }
    if not requests or "" in requests or set(requests) != set(direct):
        raise ValueError("direct arm prompt IDs must exactly match ab-requests.json")
    metadata = direct_payload.get("metadata")
    required_metadata = set(METADATA_FIELDS)
    if not isinstance(metadata, dict) or not required_metadata <= set(metadata):
        raise ValueError("direct arm metadata is incomplete")
    results: list[dict[str, Any]] = []
    for prompt_id, need in requests.items():
        item = direct[prompt_id]
        candidates = item.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError(f"direct arm candidates must be an array for {prompt_id}")
        results.append({
            "prompt_id": prompt_id,
            "request": need.get("text"),
            "candidates": candidates,
        })
    return {
        "schema_version": 2,
        "arm": "direct",
        "metadata": metadata,
        "results": results,
    }


def check_claim_traceability(
    arm_payload: dict[str, Any], repository_facts: dict[str, Any],
) -> dict[str, Any]:
    """Check existence, archive state, and exact quoted text without judging claims."""
    rows: list[dict[str, Any]] = []
    for result in arm_payload.get("results") or []:
        for candidate in result.get("candidates") or []:
            repo_name = str(candidate.get("repo") or candidate.get("full_name") or "")
            facts = repository_facts.get(repo_name.casefold()) or {}
            failures: list[str] = []
            if not facts.get("exists"):
                failures.append("repository_not_found")
            elif facts.get("archived"):
                failures.append("repository_archived")
            quote = str(candidate.get("quote") or "")
            source_term = str(candidate.get("source_term") or "")
            if quote:
                sources = [
                    source for source in facts.get("sources") or []
                    if isinstance(source, dict) and source.get("sha")
                ]
                if not any(
                    quote in str(source.get("text") or "")
                    and (not source_term or source_term in str(source.get("text") or ""))
                    for source in sources
                ):
                    failures.append("quote_not_verbatim_at_recorded_sha")
            rows.append({
                "prompt_id": result.get("prompt_id"),
                "repo": repo_name,
                "passed": not failures,
                "failures": failures,
            })
    return {
        "arm": arm_payload.get("arm"),
        "checked": len(rows),
        "passed": sum(item["passed"] for item in rows),
        "failed": sum(not item["passed"] for item in rows),
        "repositories": rows,
        "measurement": "claim_traceability_only",
    }


def build_matched_blind_pack(
    mushroom_payload: dict[str, Any], direct_payload: dict[str, Any], *,
    blind_path: Path, key_path: Path, seed: str,
) -> None:
    """Interleave the two matched arms per need behind A/B labels and write blind-key.json.

    Deliberately not a reuse of run_ab.build_blind_pack: that one is bound to the
    baseline/candidate naming and reads the categorized prompt schema, neither of
    which exists in this experiment.
    """
    arms = {
        "muse-shroom": {
            str(item.get("prompt_id") or ""): item
            for item in mushroom_payload.get("results") or []
        },
        "direct": {
            str(item.get("prompt_id") or ""): item
            for item in direct_payload.get("results") or []
        },
    }
    if (
        not arms["muse-shroom"] or "" in arms["muse-shroom"]
        or arms["muse-shroom"].keys() != arms["direct"].keys()
    ):
        raise ValueError("matched arms must cover exactly the same need IDs")
    rng = random.Random(seed)
    cases: list[dict[str, Any]] = []
    mappings: dict[str, dict[str, str]] = {}
    for need_id, mushroom_item in arms["muse-shroom"].items():
        request = mushroom_item.get("request")
        if arms["direct"][need_id].get("request") != request:
            raise ValueError(f"arms judged different request text for {need_id}")
        order = list(ARMS)
        rng.shuffle(order)
        mappings[need_id] = {"A": order[0], "B": order[1]}
        cases.append({
            "need_id": need_id,
            "request": request,
            "lists": {
                "A": list(arms[order[0]][need_id].get("candidates") or []),
                "B": list(arms[order[1]][need_id].get("candidates") or []),
            },
        })
    blind_path.parent.mkdir(parents=True, exist_ok=True)
    blind_path.write_text(json.dumps({
        "schema_version": 1, "stage": "matched_ab", "cases": cases,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps({
        "schema_version": 1,
        "mappings": mappings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_repository_facts(
    arm_payloads: list[dict[str, Any]], client: GitHubClient,
) -> dict[str, Any]:
    """Fetch existence, archive state, and README sources for every repository
    either arm names.

    Read-only: only `repository()` and `readme()` are called, and each lowercased
    name is fetched once no matter how many candidates cite it. A repository the
    API cannot see records `exists: false`; an existing one records its archived
    flag and, when a README exists, one source keyed by the README blob sha that
    claimed quotes are verified against.
    """
    names: dict[str, str] = {}
    for payload in arm_payloads:
        for result in payload.get("results") or []:
            for candidate in result.get("candidates") or []:
                repo_name = str(candidate.get("repo") or candidate.get("full_name") or "").strip()
                if repo_name and repo_name.casefold() not in names:
                    names[repo_name.casefold()] = repo_name
    facts: dict[str, Any] = {}
    for key, repo_name in sorted(names.items()):
        try:
            repository = client.repository(repo_name).data or {}
        except GitHubNotFoundError:
            facts[key] = {"exists": False}
            continue
        sources: list[dict[str, Any]] = []
        try:
            readme = client.readme(repo_name).data or {}
        except GitHubNotFoundError:
            readme = {}
        if isinstance(readme, dict) and readme.get("sha"):
            sources.append({"sha": str(readme["sha"]), "text": str(readme.get("text") or "")})
        facts[key] = {"exists": True, "archived": bool(repository.get("archived")), "sources": sources}
    return facts


def _adapt_direct_command(args: argparse.Namespace) -> None:
    payload = adapt_direct_arm(
        json.loads(args.requests.read_text(encoding="utf-8")),
        json.loads(args.input.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_claims_command(args: argparse.Namespace) -> None:
    payload = check_claim_traceability(
        json.loads(args.arm.read_text(encoding="utf-8")),
        json.loads(args.repository_facts.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_facts_command(args: argparse.Namespace) -> None:
    arm_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in args.arms]
    store = Store(args.data_dir or (ROOT / "evaluation" / "results" / "facts-data"))
    try:
        facts = collect_repository_facts(arm_payloads, GitHubClient(store))
    finally:
        store.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")


def _schedule_command(args: argparse.Namespace) -> None:
    payload = build_schedule(args.seed, args.reps)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "ok": True, "seed": args.seed, "repetitions": args.reps,
        "runs": len(payload["runs"]), "schedule": str(args.output),
    }, ensure_ascii=False, indent=2))


def _blind_command(args: argparse.Namespace) -> None:
    build_matched_blind_pack(
        json.loads(args.muse_shroom.read_text(encoding="utf-8")),
        json.loads(args.direct.read_text(encoding="utf-8")),
        blind_path=args.output, key_path=args.key, seed=args.seed,
    )
    print(json.dumps({
        "ok": True, "seed": args.seed,
        "blind_review": str(args.output), "blind_key": str(args.key),
    }, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Matched-pair A/B tooling: write the run schedule, adapt direct-host"
                    " output, build the blind pack, check claims, and collect"
                    " repository facts",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    schedule = subparsers.add_parser(
        "schedule", help="write the seeded run manifest the capture follows",
    )
    schedule.add_argument("--seed", default=DEFAULT_SEED)
    schedule.add_argument("--reps", type=int, default=1)
    schedule.add_argument("--output", type=Path, required=True)
    schedule.set_defaults(handler=_schedule_command)
    direct = subparsers.add_parser("adapt-direct", help="validate direct-host structured output")
    direct.add_argument("--requests", type=Path, required=True)
    direct.add_argument("--input", type=Path, required=True)
    direct.add_argument("--output", type=Path, required=True)
    direct.set_defaults(handler=_adapt_direct_command)
    claims = subparsers.add_parser("check-claims", help="check repository and quote facts")
    claims.add_argument("--arm", type=Path, required=True)
    claims.add_argument("--repository-facts", type=Path, required=True)
    claims.add_argument("--output", type=Path, required=True)
    claims.set_defaults(handler=_check_claims_command)
    facts = subparsers.add_parser(
        "collect-facts", help="fetch existence, archive state, and README sources for named repositories",
    )
    facts.add_argument("--arms", type=Path, nargs="+", required=True)
    facts.add_argument("--output", type=Path, required=True)
    facts.add_argument("--data-dir", type=Path, default=None)
    facts.set_defaults(handler=_collect_facts_command)
    blind = subparsers.add_parser(
        "blind", help="interleave both arms per need behind A/B labels and write the key",
    )
    blind.add_argument("--muse-shroom", type=Path, required=True)
    blind.add_argument("--direct", type=Path, required=True)
    blind.add_argument("--output", type=Path, required=True)
    blind.add_argument("--key", type=Path, required=True)
    blind.add_argument("--seed", default=DEFAULT_SEED)
    blind.set_defaults(handler=_blind_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
