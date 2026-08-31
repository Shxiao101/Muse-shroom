from __future__ import annotations

import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"


def main() -> int:
    cassette = FIXTURES / "boundary-ci-v1.json.gz"
    if cassette.exists():
        cassette.unlink()
    with tempfile.TemporaryDirectory(prefix="muse-shroom-ci-fixture-") as temporary:
        root = Path(temporary)
        common = [
            sys.executable, str(ROOT / "evaluation" / "version_worker.py"),
            "--source-root", str(ROOT),
            "--prompts", str(FIXTURES / "boundary-ci-prompts.json"),
            "--cassette", str(cassette), "--mode", "capture",
            "--search-interval", "0", "--synthetic-fixture",
        ]
        subprocess.run([
            *common, "--output", str(root / "single.json"),
            "--data-dir", str(root / "single"), "--label", "ci-single",
        ], cwd=ROOT, check=True)
        subprocess.run([
            *common, "--output", str(root / "agentic.json"),
            "--data-dir", str(root / "agentic"), "--label", "ci-agentic",
            "--agentic", "--agentic-iterations", "2", "--boundary-rank",
        ], cwd=ROOT, check=True)
    with gzip.open(cassette, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["captured_at"] = "2026-01-01T00:00:00+00:00"
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    with gzip.GzipFile(filename=str(cassette), mode="wb", mtime=0) as handle:
        handle.write(encoded)
    print(cassette)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
