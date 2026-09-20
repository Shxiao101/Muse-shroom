"""What a fresh clone needs in order to work.

Someone who clones the repository reads the README and runs what it shows. These
guards keep that path honest: the version it announces is the version it ships, and
every repository file it names is actually there.
"""
import re
import unittest
from pathlib import Path

from muse_shroom import __version__

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
# Paths the README points at inside this repository, as opposed to files a command
# writes (search.json), a host's own config (~/.codex/config.toml), or a URL.
REPO_PATH = re.compile(r"(?<![\w./-])((?:examples|skills|docs|evaluation|src|tests)/[\w./-]+)")


class RepositoryLayoutTests(unittest.TestCase):
    def test_the_readme_announces_the_version_it_ships(self):
        heading = README.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(heading, f"# Muse-shroom {__version__}")

    def test_pyproject_and_the_package_agree_on_the_version(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', pyproject)

    def test_every_repository_file_the_readme_names_exists(self):
        # The README also names local working directories such as evaluation/results/,
        # which .gitignore keeps out of a clone on purpose.
        ignored = tuple(
            line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip().endswith("/")
        )
        text = README.read_text(encoding="utf-8")
        missing = sorted({
            path for path in REPO_PATH.findall(text)
            if not path.startswith(ignored) and not (ROOT / path.rstrip(".,)")).exists()
        })
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
