from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillStructureTest(unittest.TestCase):
    def test_core_skill_stays_concise(self):
        lines = (ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()
        self.assertLessEqual(len(lines), 250)

    def test_local_references_from_skill_exist(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        targets = re.findall(r"\[[^]]+]\((references/[^)]+\.md)\)", skill)
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((ROOT / target).is_file())

    def test_controlled_writing_has_four_groups_and_twelve_examples(self):
        reference = (
            ROOT / "references" / "controlled-technical-chinese.md"
        ).read_text(encoding="utf-8")
        for heading in ("Operating manuals", "API documentation", "Troubleshooting", "Product introductions"):
            with self.subTest(heading=heading):
                self.assertIn(f"### {heading}", reference)
        self.assertEqual(len(re.findall(r"^#### Example \d+$", reference, re.MULTILINE)), 12)

    def test_paragraph_wrapping_rule_is_documented(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (
            ROOT / "references" / "terminology-and-typography.md"
        ).read_text(encoding="utf-8")
        self.assertIn("each Chinese paragraph on one source line", skill)
        self.assertIn("## Paragraph wrapping and source line width", reference)
        self.assertIn("scripts/unwrap_md_paragraphs.py", reference)

    def test_project_override_is_explicitly_a_template(self):
        reference = (
            ROOT / "references" / "project-overrides-example.md"
        ).read_text(encoding="utf-8")
        self.assertIn("This file is only a template", reference)
        self.assertFalse((ROOT / "references" / "Project-Overrides.md").exists())


if __name__ == "__main__":
    unittest.main()
