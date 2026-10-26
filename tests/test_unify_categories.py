import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import cashbacks, unify_categories


class UnifyCategoriesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def rule(self, bank, category, mcc, title=None, *, exclude=False):
        first_alias = category if isinstance(category, str) else category[0]
        path = (
            self.root / "src" / bank / "categories"
            / cashbacks._canonical_category_filename(first_alias)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"category": category}
        if title is not None:
            entry["unified_category"] = title
        entry["exclude_mcc" if exclude else "include_mcc"] = mcc
        path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = unify_categories.main(args, root=self.root)
        return status, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def test_preview_then_scoped_write_preserves_aliases_mcc_and_other_banks(self):
        donor = self.rule("Known-ru_1", "Животные", [742, 5995], "Зоотовары")
        target = self.rule("New-kz_2", ["Питомцы", "Зоомагазины"], [742, 5995])
        untouched = self.rule("Other-by_3", "Pets", [742, 5995])
        before = {path: path.read_bytes() for path in (donor, target, untouched)}

        status, report, error = self.cli("--bank", "2")

        self.assertEqual((0, ""), (status, error))
        self.assertEqual("dry_run", report["mode"])
        self.assertEqual(0, report["written"])
        self.assertEqual(1, len(report["changes"]))
        self.assertEqual([donor.relative_to(self.root).as_posix()], report["changes"][0]["sources"])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

        status, report, error = self.cli("--bank", "New-kz_2", "--write")

        self.assertEqual((0, ""), (status, error))
        self.assertEqual(1, report["written"])
        entry = json.loads(target.read_bytes())
        self.assertEqual(["category", "unified_category", "include_mcc"], list(entry))
        self.assertEqual({
            "category": ["Питомцы", "Зоомагазины"],
            "unified_category": "Зоотовары",
            "include_mcc": [742, 5995],
        }, entry)
        self.assertEqual(before[donor], donor.read_bytes())
        self.assertEqual(before[untouched], untouched.read_bytes())
        cashbacks.load_repository(self.root)
        after = target.read_bytes()
        status, report, _ = self.cli("--bank", "2", "--write")
        self.assertEqual(0, status)
        self.assertEqual([], report["changes"])
        self.assertEqual(0, report["written"])
        self.assertEqual(after, target.read_bytes())

    def test_no_fuzzy_title_matching_or_guessing_for_unnamed_clusters(self):
        self.rule("Known-ru_1", "Same label", list(range(1, 11)), "Known title")
        self.rule("New-ru_2", "Same label", list(range(1, 10)))
        self.rule("Known-ru_1", "Unknown", [500])
        self.rule("New-ru_2", "Unknown", [500])

        status, report, _ = self.cli("--bank", "2", "--write")

        self.assertEqual(0, status)
        self.assertEqual([], report["changes"])
        self.assertEqual(2, len(report["unresolved"]))
        unnamed = next(row for row in report["unresolved"] if row["include_mcc"] == [500])
        self.assertEqual(1, len(unnamed["matches"]))
        self.assertIsNone(unnamed["matches"][0]["unified_category"])

    def test_same_bank_alone_is_not_a_donor(self):
        self.rule("Known-ru_1", "Named", [4121], "Такси")
        self.rule("Known-ru_1", "Unnamed", [4121])

        status, report, _ = self.cli("--write")

        self.assertEqual(0, status)
        self.assertEqual([], report["changes"])
        self.assertEqual(1, len(report["unresolved"]))

    def test_exclusions_get_all_purchases_regardless_of_mcc(self):
        first = self.rule("New-ru_1", "Остальное", [1], exclude=True)
        second = self.rule("New-by_2", "На всё!", [2, 3], exclude=True)

        status, report, _ = self.cli("--write")

        self.assertEqual(0, status)
        self.assertEqual(2, report["written"])
        for path, mcc in [(first, [1]), (second, [2, 3])]:
            entry = json.loads(path.read_bytes())
            self.assertEqual("Все покупки", entry["unified_category"])
            self.assertEqual(mcc, entry["exclude_mcc"])

    def test_conflict_blocks_selected_write_and_identifies_source_rules(self):
        self.rule("Known-ru_1", "First", [1], "One")
        self.rule("Known-ru_2", "Second", [1], "Two")
        target = self.rule("New-ru_3", "Third", [1])
        otherwise_safe = self.rule("New-ru_3", "All", [2], exclude=True)
        before = {path: path.read_bytes() for path in (target, otherwise_safe)}

        status, report, _ = self.cli("--bank", "3", "--write")

        self.assertEqual(2, status)
        self.assertEqual(0, report["written"])
        self.assertEqual(1, len(report["changes"]))
        self.assertEqual(1, len(report["conflicts"]))
        conflict = report["conflicts"][0]
        self.assertEqual([1], conflict["include_mcc"])
        self.assertEqual({"One", "Two", None}, {row["unified_category"] for row in conflict["rules"]})
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_existing_inconsistent_title_is_reported_without_overwriting(self):
        self.rule("Known-ru_1", "First", [1], "One")
        target = self.rule("New-ru_2", "Second", [1], "Two")
        before = target.read_bytes()

        status, report, _ = self.cli("--bank", "2", "--write")

        self.assertEqual(2, status)
        self.assertEqual([], report["changes"])
        self.assertEqual(before, target.read_bytes())

    def test_nonstandard_existing_exclusion_title_is_a_conflict(self):
        path = self.rule("New-ru_1", "All", [1], "Existing", exclude=True)
        before = path.read_bytes()

        status, report, _ = self.cli("--write")

        self.assertEqual(2, status)
        self.assertEqual("Все покупки", report["conflicts"][0]["expected"])
        self.assertEqual(before, path.read_bytes())

    def test_unrelated_conflict_does_not_block_scoped_write(self):
        self.rule("Known-ru_1", "First", [1], "One")
        self.rule("Known-ru_2", "Second", [1], "Two")
        self.rule("New-ru_3", "All", [2], exclude=True)

        status, report, _ = self.cli("--bank", "3", "--write")

        self.assertEqual(0, status)
        self.assertEqual([], report["conflicts"])
        self.assertEqual(1, report["written"])

    def test_pending_banks_are_distinct_donors_and_targets(self):
        self.rule("Pending-by_", "Taxi", [4121], "Такси")
        self.rule("New-ru_2", "Cabs", [4121])
        self.rule("Other-kz_", "Cars", [4121])

        status, report, _ = self.cli("--bank", "2", "--bank", "Other-kz_", "--write")

        self.assertEqual(0, status)
        self.assertEqual(2, report["written"])
        self.assertEqual(2, len(cashbacks.load_repository(self.root).pending_banks))

    def test_catalog_allows_distinct_mcc_sets_to_share_a_title_and_separates_modes(self):
        self.rule("Known-ru_1", "First", [1], "Shared")
        self.rule("Known-by_2", "Second", [2], "Shared")
        self.rule("Known-by_2", "Third", [1], "Shared")
        self.rule("Known-ru_1", "All", [1], "Все покупки", exclude=True)

        status, catalog, _ = self.cli("--catalog")

        self.assertEqual(0, status)
        shared = next(row for row in catalog if row["unified_category"] == "Shared")
        self.assertEqual([[1], [2]], [group["include_mcc"] for group in shared["groups"]])
        self.assertEqual(2, len(shared["groups"][0]["rules"]))
        all_purchases = next(row for row in catalog if row["unified_category"] == "Все покупки")
        self.assertEqual([1], all_purchases["groups"][0]["exclude_mcc"])

    def test_strict_validation_precedes_any_writes(self):
        valid = self.rule("First-ru_1", "All", [1], exclude=True)
        self.rule("Invalid-ru_2", "Bad", [2, 1])
        before = valid.read_bytes()

        status, report, error = self.cli("--write")

        self.assertEqual(1, status)
        self.assertIsNone(report)
        self.assertIn("strictly ascending", error)
        self.assertEqual(before, valid.read_bytes())

    def test_unknown_bank_selector_fails_instead_of_writing_all_banks(self):
        path = self.rule("Known-ru_1", "All", [1], exclude=True)
        before = path.read_bytes()

        status, report, error = self.cli("--bank", "12345", "--write")

        self.assertEqual(1, status)
        self.assertIsNone(report)
        self.assertIn("unknown bank", error)
        self.assertEqual(before, path.read_bytes())

    def test_changed_donor_blocks_write_of_stale_plan(self):
        donor = self.rule("Known-ru_1", "Known", [1], "Old")
        target = self.rule("New-ru_2", "New", [1])
        before = target.read_bytes()
        rules, _ = unify_categories.load_rules(self.root)
        plan = unify_categories.plan_changes(rules, {"New-ru_2"})
        donor.write_text(donor.read_text().replace("Old", "New"))

        with self.assertRaisesRegex(cashbacks.DataError, "source changed"):
            unify_categories.write_changes(self.root, rules, plan["changes"])

        self.assertEqual(before, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
