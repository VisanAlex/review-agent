from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from review_agent import __version__


REPOSITORY = Path(__file__).parents[1]
PLUGIN = REPOSITORY / "plugins" / "review-agent"
CANONICAL_SKILL = (
    REPOSITORY / "src" / "review_agent" / "skill_template" / "review-agent"
)
PLUGIN_SKILL = PLUGIN / "skills" / "review-agent"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def relative_files(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


class PluginDistributionTests(unittest.TestCase):
    def test_codex_plugin_and_marketplace_point_to_bundled_skill(self) -> None:
        manifest = read_json(PLUGIN / ".codex-plugin" / "plugin.json")
        marketplace = read_json(
            REPOSITORY / ".agents" / "plugins" / "marketplace.json"
        )

        self.assertEqual(manifest["name"], "review-agent")
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(
            manifest["repository"], "https://github.com/VisanAlex/review-agent"
        )
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(marketplace["name"], "review-agent")
        self.assertEqual(marketplace["plugins"][0]["name"], "review-agent")
        self.assertEqual(
            marketplace["plugins"][0]["source"]["path"],
            "./plugins/review-agent",
        )

    def test_claude_plugin_and_marketplace_point_to_same_bundle(self) -> None:
        manifest = read_json(PLUGIN / ".claude-plugin" / "plugin.json")
        marketplace = read_json(
            REPOSITORY / ".claude-plugin" / "marketplace.json"
        )

        self.assertEqual(manifest["name"], "review-agent")
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(
            manifest["repository"], "https://github.com/VisanAlex/review-agent"
        )
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(marketplace["name"], "review-agent")
        self.assertEqual(marketplace["plugins"][0]["name"], "review-agent")
        self.assertEqual(
            marketplace["plugins"][0]["source"],
            "./plugins/review-agent",
        )

    def test_plugin_skill_is_an_exact_copy_of_canonical_skill(self) -> None:
        expected = relative_files(CANONICAL_SKILL)
        self.assertEqual(relative_files(PLUGIN_SKILL), expected)
        for relative in expected:
            self.assertEqual(
                (PLUGIN_SKILL / relative).read_bytes(),
                (CANONICAL_SKILL / relative).read_bytes(),
                relative,
            )

    def test_sync_check_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/sync_plugin_skill.py", "--check"],
            cwd=REPOSITORY,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
