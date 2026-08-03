from __future__ import annotations

import configparser
import unittest
from pathlib import Path, PurePosixPath

from review_agent import __version__
from review_agent.skills import MANAGED_FILES


REPOSITORY = Path(__file__).parents[1]
LEGACY_CONFIG = REPOSITORY / "setup.cfg"
MODERN_CONFIG = REPOSITORY / "pyproject.toml"


def modern_section(name: str) -> dict[str, str]:
    text = MODERN_CONFIG.read_text(encoding="utf-8")
    section = text.split(f"[{name}]", 1)[1].split("\n[", 1)[0]
    values: dict[str, str] = {}
    for line in section.splitlines():
        if " = " in line:
            key, value = line.split(" = ", 1)
            values[key] = value.strip('"')
    return values


class LegacyPackagingTests(unittest.TestCase):
    def config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser()
        loaded = parser.read(LEGACY_CONFIG, encoding="utf-8")
        self.assertEqual(loaded, [str(LEGACY_CONFIG)])
        return parser

    def test_legacy_metadata_matches_the_modern_package(self) -> None:
        parser = self.config()
        project = modern_section("project")
        scripts = modern_section("project.scripts")
        self.assertEqual(parser["metadata"]["name"], project["name"])
        self.assertEqual(parser["metadata"]["version"], project["version"])
        self.assertEqual(project["version"], __version__)
        self.assertEqual(parser["metadata"]["license"], "MIT")
        self.assertEqual(
            parser["options"]["python_requires"], project["requires-python"]
        )
        self.assertIn(
            f"review-agent = {scripts['review-agent']}",
            parser["options.entry_points"]["console_scripts"],
        )

    def test_legacy_package_data_covers_every_managed_skill_file(self) -> None:
        parser = self.config()
        patterns = set(parser["options.package_data"]["review_agent"].split())
        expected = {
            "skill_template/review-agent/*.md",
            "skill_template/review-agent/agents/*.yaml",
            "skill_template/review-agent/references/*.md",
        }
        self.assertEqual(patterns, expected)
        for relative_path in MANAGED_FILES:
            packaged_path = PurePosixPath(
                "skill_template", "review-agent", relative_path
            )
            self.assertTrue(
                any(packaged_path.match(pattern) for pattern in patterns),
                f"Legacy package data does not cover {packaged_path}",
            )


if __name__ == "__main__":
    unittest.main()
