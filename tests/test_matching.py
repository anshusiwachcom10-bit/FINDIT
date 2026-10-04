import unittest

from database.matching import MIN_MATCH_SCORE, find_matches, score_pair


def report(
    report_id,
    report_type,
    *,
    title="Brown leather wallet",
    category="Wallets",
    description="Brown leather wallet with three card slots and brass clasp",
    location="Central Library",
    event_date="2026-10-02",
    user_id=None,
    status="open",
):
    return {
        "id": report_id,
        "type": report_type,
        "title": title,
        "category": category,
        "description": description,
        "location": location,
        "event_date": event_date,
        "user_id": user_id,
        "status": status,
    }


class MatchingAlgorithmTests(unittest.TestCase):
    def test_similar_wallet_descriptions_get_explainable_strong_match(self):
        lost = report(
            1,
            "lost",
            description="Brown leather wallet with three card slots",
        )
        found = report(
            2,
            "found",
            title="Dark brown wallet",
            description="A dark brown leather wallet, three compartments",
        )

        result = score_pair(lost, found)

        self.assertGreaterEqual(result["score"], 70)
        self.assertEqual(result["level"], "Strong Match")
        self.assertIn("Similar description", result["reasons"])
        self.assertIn("Similar color: brown", result["reasons"])
        self.assertIn("Similar material: leather", result["reasons"])
        self.assertEqual(sum(result["weights"].values()), 100)
        self.assertEqual(result, score_pair(lost, found))

    def test_different_categories_are_capped_below_display_threshold(self):
        lost = report(1, "lost", category="Wallets")
        found = report(2, "found", category="Backpacks")

        result = score_pair(lost, found)

        self.assertLess(result["score"], MIN_MATCH_SCORE)
        self.assertEqual(find_matches(lost, [found]), [])

    def test_same_category_but_unrelated_descriptions_do_not_match(self):
        lost = report(
            1,
            "lost",
            title="Wallet",
            description="Small black leather bifold with a torn corner",
            location="West Station",
            event_date="2026-08-01",
        )
        found = report(
            2,
            "found",
            title="Wallet",
            description="Large silver metal engraved travel case",
            location="East Airport",
            event_date="2026-10-02",
        )

        self.assertLess(score_pair(lost, found)["score"], MIN_MATCH_SCORE)

    def test_similar_location_is_a_textual_signal_without_invented_distance(self):
        lost = report(1, "lost", location="Central Library")
        nearby = report(2, "found", location="Central Library East Entrance")
        far = report(3, "found", location="North Airport Terminal")

        near_score = score_pair(lost, nearby)
        far_score = score_pair(lost, far)

        self.assertGreater(near_score["components"]["location"], far_score["components"]["location"])
        self.assertTrue(any("location" in reason for reason in near_score["reasons"]))
        self.assertFalse(any("location" in reason for reason in far_score["reasons"]))
        self.assertNotIn("km", " ".join(near_score["reasons"]).lower())

    def test_close_dates_score_higher_than_far_dates(self):
        lost = report(1, "lost", event_date="2026-10-02")
        close = report(2, "found", event_date="2026-10-04")
        far = report(3, "found", event_date="2026-10-25")

        close_score = score_pair(lost, close)
        far_score = score_pair(lost, far)

        self.assertGreater(close_score["components"]["date"], far_score["components"]["date"])
        self.assertIn("Report dates 2 days apart", close_score["reasons"])
        self.assertEqual(far_score["date_difference_days"], 23)

    def test_missing_optional_fields_are_safe_and_do_not_inflate_score(self):
        lost = report(
            1,
            "lost",
            title="wallet",
            description=None,
            location=None,
            event_date=None,
        )
        found = report(
            2,
            "found",
            title="wallet",
            description="",
            location="",
            event_date="",
        )

        result = score_pair(lost, found)

        self.assertEqual(result["components"]["date"], 0)
        self.assertEqual(result["components"]["description"], 0)
        self.assertEqual(result["components"]["location"], 0)
        self.assertLess(result["score"], MIN_MATCH_SCORE)

    def test_only_opposite_open_report_types_are_compared(self):
        lost = report(1, "lost", user_id=8)
        self.assertIsNone(score_pair(lost, report(2, "lost")))
        self.assertIsNone(score_pair(lost, report(3, "found", status="resolved")))
        self.assertIsNone(score_pair(lost, report(4, "found", user_id=8)))

    def test_multiple_candidates_are_sorted_by_deterministic_score(self):
        lost = report(1, "lost")
        candidates = [
            report(3, "found", title="Wallet", description="A black nylon bag"),
            report(2, "found"),
            report(4, "found", title="Wallet", category="Backpacks"),
        ]

        results = find_matches(lost, candidates)

        self.assertEqual([result["item"]["id"] for result in results], [2, 3])
        self.assertGreaterEqual(results[0]["score"], results[1]["score"])
        self.assertTrue(all(result["score"] >= MIN_MATCH_SCORE for result in results))


if __name__ == "__main__":
    unittest.main()
