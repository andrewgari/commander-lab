import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import changelog as cl  # noqa: E402

FIX = open(os.path.join(ROOT, "tests", "fixtures", "changelog_sample.md")).read()


class TestChangelogScript(unittest.TestCase):
    def test_extract_unreleased_omits_empty_and_keeps_multiline(self):
        out = cl.extract(FIX, "Unreleased")
        self.assertIn("### Added", out)
        self.assertIn("### Fixed", out)
        self.assertNotIn("### Changed", out)
        self.assertIn("  Continued line.", out)
        self.assertNotIn("## [", out)

    def test_extract_version_is_isolated(self):
        out = cl.extract(FIX, "v9.9.2")
        self.assertIn("Something changed.", out)
        self.assertNotIn("New thing", out)

    def test_maintenance_line_for_entryless_section(self):
        self.assertEqual(cl.extract(FIX, "v9.9.1"), cl.MAINTENANCE + "\n")

    def test_missing_section_raises(self):
        with self.assertRaises(cl.ChangelogError):
            cl.extract(FIX, "v0.0.0-missing")

    def test_delta_body_only_new_entries(self):
        old = FIX.replace("- New thing for users.\n", "")
        out = cl.delta_body(old, FIX)
        self.assertIn("New thing", out)
        self.assertNotIn("Old bug", out)

    def test_delta_body_no_new_entries_is_maintenance(self):
        self.assertEqual(cl.delta_body(FIX, FIX), cl.MAINTENANCE + "\n")

    def test_delta_body_without_old_file(self):
        self.assertIn("New thing", cl.delta_body(None, FIX))

    def test_promote_creates_dated_section_and_links(self):
        out = cl.promote(FIX, "v9.9.3", "2030-03-03")
        self.assertRegex(out, r"(?m)^## \[Unreleased\]\n\n## \[v9\.9\.3\] - 2030-03-03$")
        self.assertIn("[Unreleased]: https://example.test/r/compare/v9.9.3...HEAD", out)
        self.assertIn("[v9.9.3]: https://example.test/r/compare/v9.9.2...v9.9.3", out)
        self.assertEqual(cl.validate(out), [])
        self.assertIn("New thing", cl.extract(out, "v9.9.3"))

    def test_promote_empty_unreleased_fails(self):
        once = cl.promote(FIX, "v9.9.3", "2030-03-03")
        with self.assertRaises(cl.ChangelogError):
            cl.promote(once, "v9.9.4", "2030-03-04")

    def test_validate_flags_problems(self):
        bad = FIX.replace("### Fixed", "### Misc").replace("2030-02-02", "2029-02-02")
        errs = "\n".join(cl.validate(bad))
        self.assertIn("unknown heading", errs)
        self.assertIn("newest-first", errs)

    def test_validate_flags_noise(self):
        bad = FIX.replace("New thing for users.", "Merged PR #123 and bumped version")
        self.assertTrue(any("noisy" in e for e in cl.validate(bad)))

    def test_repo_changelog_is_valid(self):
        real = open(os.path.join(ROOT, "CHANGELOG.md")).read()
        self.assertEqual(cl.validate(real), [])

    def test_renders_in_release_body_renderer(self):
        from src.server.app.main import render_release_body
        html = render_release_body(cl.extract(FIX, "Unreleased"))
        self.assertIn("<h3>Added</h3>", html)
        self.assertIn("<li>New thing for users.", html)


if __name__ == "__main__":
    unittest.main()
