import unittest

from shipnotes.rules import classify, dedupe, parse_text


def c(s, author=""):
    return classify({"subject": s, "author": author})


class RulesTest(unittest.TestCase):
    def test_internal_conventional_types_are_dropped(self):
        for s in ["chore: bump version", "ci: cache pip", "refactor(core): split module",
                  "test: add cases", "build(deps): bump x", "docs: fix typo", "style: black"]:
            self.assertFalse(c(s).kept, s)

    def test_internal_scopes_are_dropped(self):
        self.assertFalse(c("fix(deps): update rust crate serde to 1.0.200").kept)

    def test_sections(self):
        self.assertEqual(c("feat: dark mode").section, "new")
        self.assertEqual(c("fix(api): 500 on empty body").section, "fixed")
        self.assertEqual(c("perf: faster startup").section, "improved")
        self.assertEqual(c("Add CSV export").section, "new")
        self.assertEqual(c("Fix crash on resize").section, "fixed")
        self.assertEqual(c("Make sidebar collapsible").section, "improved")

    def test_heuristic_internal(self):
        for s in ["Merge pull request #12 from a/b", "Bump lodash from 4.17.20 to 4.17.21",
                  "Fix typo in README", "v1.2.3", "Update dependencies", "Run prettier"]:
            self.assertFalse(c(s).kept, s)

    def test_breaking_survives_even_if_internal_type(self):
        x = c("refactor!: drop Python 3.8 support")
        self.assertTrue(x.kept and x.breaking)

    def test_bot_author_dropped(self):
        self.assertFalse(c("Update foo", author="dependabot[bot]").kept)

    def test_pr_number_extracted(self):
        x = c("feat: add webhooks (#431)")
        self.assertEqual((x.pr, x.subject), ("431", "add webhooks"))

    def test_parse_oneline_and_full_log(self):
        self.assertEqual(parse_text("a1b2c3d feat: x\n- fix: y\n")[0]["subject"], "feat: x")
        full = "commit 0123456789abcdef\nAuthor: A <a@b>\nDate: now\n\n    feat: z\n\n    body\n"
        self.assertEqual(parse_text(full)[0]["subject"], "feat: z")

    def test_dedupe(self):
        xs = dedupe([c("feat: add export"), c("feat: Add export.")])
        self.assertEqual(sum(x.kept for x in xs), 1)


if __name__ == "__main__":
    unittest.main()


class FallbackTest(unittest.TestCase):
    def test_fallback_reads_like_a_changelog(self):
        from shipnotes.rules import fallback_note
        self.assertEqual(fallback_note(c("fix: KeyError in logs output")), "Fixed KeyError in logs output.")
        self.assertEqual(fallback_note(c("Fix for CRLF handling")), "Fixed CRLF handling.")
        self.assertEqual(fallback_note(c("feat: dark mode")), "Added dark mode.")
        self.assertEqual(fallback_note(c("Keep tool params named title")), "Now keeps tool params named title.")

    def test_internal_tooling(self):
        for s in ["Added types to make mypy happy", "Fix for Sphinx warnings",
                  "Fix for CRLF test failure", "Replace runner.isolated_filesystem() with tmp_dir fixture"]:
            self.assertFalse(c(s).kept, s)


class GuardTest(unittest.TestCase):
    def test_must_keep_and_defluff(self):
        from shipnotes.llm import clean_note, defluff, must_keep
        self.assertIn("AsyncResponse.log_to_db", must_keep("Fix AsyncResponse.log_to_db"))
        self.assertIn("--watch", must_keep("add --watch mode"))
        self.assertIsNone(clean_note("Fixed an error in the response log.", "en", "Fix AsyncResponse.log_to_db"))
        self.assertEqual(defluff("Fixed a critical error in the parser, ensuring proper handling."),
                         "Fixed an error in the parser.")


class FragmentTest(unittest.TestCase):
    def test_code_noun_phrase_gets_a_verb(self):
        from shipnotes.rules import fallback_note
        self.assertEqual(fallback_note(c("model.supports_conversation property, closes #1692")),
                         "Updated model.supports_conversation property.")
