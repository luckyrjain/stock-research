import unittest

from signals.filings_classifier import (
    classify_corporate_actions,
    classify_filings,
    classify_rating_action,
    extract_next_results_date,
)


class ClassifyCorporateActionsTest(unittest.TestCase):
    def test_dividend_filing_is_classified(self) -> None:
        filings = [{"title": "Recommendation of Dividend", "desc": "", "category": "Dividend", "date": "01-Jan-2026"}]
        result = classify_corporate_actions(filings)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "dividend")

    def test_buyback_filing_is_classified(self) -> None:
        filings = [{"title": "Board approves Buyback of shares", "desc": "", "category": "", "date": "01-Jan-2026"}]
        result = classify_corporate_actions(filings)
        self.assertEqual(result[0]["type"], "buyback")

    def test_bonus_issue_is_classified(self) -> None:
        filings = [{"title": "Issue of Bonus Shares in ratio 1:1", "desc": "", "category": "", "date": "01-Jan-2026"}]
        result = classify_corporate_actions(filings)
        self.assertEqual(result[0]["type"], "bonus")

    def test_stock_split_is_classified(self) -> None:
        filings = [{"title": "Sub-division of equity shares", "desc": "", "category": "", "date": "01-Jan-2026"}]
        result = classify_corporate_actions(filings)
        self.assertEqual(result[0]["type"], "split")

    def test_unrelated_filing_is_not_classified(self) -> None:
        filings = [{"title": "Newspaper Publication", "desc": "", "category": "", "date": "01-Jan-2026"}]
        self.assertEqual(classify_corporate_actions(filings), [])

    def test_sorted_newest_first(self) -> None:
        filings = [
            {"title": "Dividend declared", "desc": "", "category": "", "date": "01-Jan-2026"},
            {"title": "Buyback announced", "desc": "", "category": "", "date": "15-Feb-2026"},
        ]
        result = classify_corporate_actions(filings)
        self.assertEqual(result[0]["type"], "buyback")
        self.assertEqual(result[1]["type"], "dividend")

    def test_unparseable_date_does_not_raise_and_sorts_last(self) -> None:
        filings = [
            {"title": "Dividend declared", "desc": "", "category": "", "date": "not-a-date"},
            {"title": "Buyback announced", "desc": "", "category": "", "date": "15-Feb-2026"},
        ]
        result = classify_corporate_actions(filings)
        self.assertEqual(result[0]["type"], "buyback")

    def test_non_dict_filing_is_skipped_not_raised(self) -> None:
        self.assertEqual(classify_corporate_actions([None, "garbage", 42]), [])

    def test_none_title_desc_do_not_raise(self) -> None:
        filings = [{"title": None, "desc": None, "category": None, "date": None}]
        self.assertEqual(classify_corporate_actions(filings), [])

    def test_first_matching_category_wins_when_multiple_keywords_present(self) -> None:
        # "dividend" is checked before "buyback" in _CORPORATE_ACTION_KEYWORDS —
        # a filing matching both only contributes one entry.
        filings = [{"title": "Dividend and Buyback both approved", "desc": "", "category": "", "date": "01-Jan-2026"}]
        result = classify_corporate_actions(filings)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "dividend")


class ClassifyRatingActionTest(unittest.TestCase):
    def test_upgrade_by_known_agency_is_detected(self) -> None:
        filings = [{
            "title": "CRISIL upgrades rating",
            "desc": "CRISIL has upgraded the long-term rating from 'AA-' to 'AA'.",
            "category": "",
            "date": "01-Jan-2026",
        }]
        result = classify_rating_action(filings)
        self.assertIsNotNone(result)
        self.assertEqual(result["agency"], "Crisil")
        self.assertEqual(result["action"], "upgrade")
        self.assertEqual(result["from_rating"], "AA-")
        self.assertEqual(result["to_rating"], "AA")

    def test_downgrade_is_detected(self) -> None:
        filings = [{"title": "ICRA downgrades", "desc": "ICRA has downgraded the rating.", "category": "", "date": "01-Jan-2026"}]
        result = classify_rating_action(filings)
        self.assertEqual(result["action"], "downgrade")

    def test_reaffirmed_is_detected(self) -> None:
        filings = [{"title": "CARE reaffirms rating", "desc": "", "category": "", "date": "01-Jan-2026"}]
        result = classify_rating_action(filings)
        self.assertEqual(result["action"], "reaffirmed")

    def test_from_to_absent_when_not_cleanly_parseable(self) -> None:
        filings = [{"title": "CRISIL upgrades the company's rating", "desc": "", "category": "", "date": "01-Jan-2026"}]
        result = classify_rating_action(filings)
        self.assertIsNone(result["from_rating"])
        self.assertIsNone(result["to_rating"])

    def test_from_to_extracted_even_with_no_trailing_punctuation(self) -> None:
        # Regression test: the "from X to Y" phrase can legitimately be the
        # very tail of the joined title+desc+category text (category is
        # joined last) with nothing after it — a regex requiring a trailing
        # whitespace/period/comma would silently fail to extract from_rating/
        # to_rating even though the phrase itself is perfectly clean.
        filings = [{"title": "CRISIL Rating Action", "desc": "", "category": "downgraded from AA+ to AA-", "date": "01-Jan-2026"}]
        result = classify_rating_action(filings)
        self.assertEqual(result["from_rating"], "AA+")
        self.assertEqual(result["to_rating"], "AA-")

    def test_no_rating_agency_mention_returns_none(self) -> None:
        filings = [{"title": "Board Meeting Intimation", "desc": "", "category": "", "date": "01-Jan-2026"}]
        self.assertIsNone(classify_rating_action(filings))

    def test_agency_mention_without_action_keyword_returns_none(self) -> None:
        filings = [{"title": "CRISIL research report cited", "desc": "", "category": "", "date": "01-Jan-2026"}]
        self.assertIsNone(classify_rating_action(filings))

    def test_most_recent_rating_filing_wins(self) -> None:
        filings = [
            {"title": "CRISIL downgrades", "desc": "", "category": "", "date": "01-Jan-2026"},
            {"title": "ICRA upgrades", "desc": "", "category": "", "date": "15-Feb-2026"},
        ]
        result = classify_rating_action(filings)
        self.assertEqual(result["agency"], "Icra")
        self.assertEqual(result["action"], "upgrade")

    def test_empty_filings_returns_none(self) -> None:
        self.assertIsNone(classify_rating_action([]))

    def test_bare_care_alias_does_not_match_healthcare_or_customer_care(self) -> None:
        # Regression test for an adversarial-review finding: the bare "care"
        # alias in _RATING_AGENCIES (a short form of CARE Ratings) previously
        # matched as a plain substring, so it fired on any text containing
        # "care" anywhere -- including inside completely unrelated words like
        # "healthcare". Combined with "upgrade" (both a rating-action keyword
        # and an ordinary English word), a routine filing about a facility
        # upgrade to improve employee healthcare was fabricated into a fake
        # "CARE upgraded" credit-rating action -- a direct violation of this
        # codebase's "never invent" convention.
        filings = [{
            "title": "Company announces facility upgrade to improve healthcare delivery for employees",
            "desc": "",
            "category": "",
            "date": "20-10-2025",
        }]
        self.assertIsNone(classify_rating_action(filings))

        filings2 = [{
            "title": "Company expands customer care center capacity",
            "desc": "The upgrade will improve response times.",
            "category": "",
            "date": "20-10-2025",
        }]
        self.assertIsNone(classify_rating_action(filings2))

    def test_bare_care_alias_still_matches_when_rating_context_present(self) -> None:
        # The disambiguation guard must not break the legitimate case this
        # alias exists for -- a filing mentioning just "CARE" (not the full
        # "CARE Ratings") alongside an actual rating action.
        filings = [{"title": "CARE reaffirms rating", "desc": "", "category": "", "date": "01-Jan-2026"}]
        result = classify_rating_action(filings)
        self.assertIsNotNone(result)
        self.assertEqual(result["agency"], "Care")
        self.assertEqual(result["action"], "reaffirmed")


class ExtractNextResultsDateTest(unittest.TestCase):
    def test_extracts_slash_date_after_filing_date(self) -> None:
        filings = [{
            "title": "Board Meeting Intimation",
            "desc": "The Board will meet on 15/08/2026 to consider financial results.",
            "category": "",
            "date": "01-Jan-2026",
        }]
        self.assertEqual(extract_next_results_date(filings), "2026-08-15")

    def test_extracts_month_name_date(self) -> None:
        filings = [{
            "title": "Board Meeting Intimation",
            "desc": "Board meeting scheduled on 15 Aug 2026 to consider quarterly results.",
            "category": "",
            "date": "01-Jan-2026",
        }]
        self.assertEqual(extract_next_results_date(filings), "2026-08-15")

    def test_no_board_meeting_filing_returns_none(self) -> None:
        filings = [{"title": "Newspaper Publication", "desc": "", "category": "", "date": "01-Jan-2026"}]
        self.assertIsNone(extract_next_results_date(filings))

    def test_board_meeting_without_results_mention_returns_none(self) -> None:
        filings = [{
            "title": "Board Meeting Intimation",
            "desc": "Board will meet on 15/08/2026 to consider other business.",
            "category": "",
            "date": "01-Jan-2026",
        }]
        self.assertIsNone(extract_next_results_date(filings))

    def test_no_date_in_text_returns_none(self) -> None:
        filings = [{
            "title": "Board Meeting Intimation",
            "desc": "Board will meet shortly to consider financial results.",
            "category": "",
            "date": "01-Jan-2026",
        }]
        self.assertIsNone(extract_next_results_date(filings))

    def test_date_earlier_than_filing_date_is_rejected(self) -> None:
        # A date extracted from the text that predates the filing itself is
        # more likely an unrelated mention than the actual meeting date.
        filings = [{
            "title": "Board Meeting Intimation",
            "desc": "Pursuant to results for FY 01/04/2025, board will meet to consider financial results.",
            "category": "",
            "date": "15-Aug-2026",
        }]
        self.assertIsNone(extract_next_results_date(filings))

    def test_second_date_match_used_when_first_predates_filing_date(self) -> None:
        # Regression test for an adversarial-review finding: a real NSE
        # board-meeting intimation conventionally mentions the (past)
        # quarter-end date the results cover before the (future) meeting
        # date, in the same numeric DD-MM-YYYY format. Only checking the
        # first regex match found the quarter-end date, correctly rejected
        # it as earlier than the filing's own date, then gave up on the
        # whole pattern instead of trying the second match -- silently
        # returning None even though the real meeting date is right there.
        filings = [{
            "title": "Board Meeting Intimation",
            "desc": (
                "Board Meeting will be held to consider the un-audited "
                "financial results for the quarter ended 30-09-2025, and "
                "the meeting is scheduled on 05-11-2025."
            ),
            "category": "",
            "date": "20-10-2025",
        }]
        self.assertEqual(extract_next_results_date(filings), "2025-11-05")

    def test_most_recent_board_meeting_filing_is_used(self) -> None:
        filings = [
            {
                "title": "Board Meeting Intimation",
                "desc": "Meeting on 01/02/2026 to consider financial results.",
                "category": "",
                "date": "01-Jan-2026",
            },
            {
                "title": "Board Meeting Intimation",
                "desc": "Meeting on 15/05/2026 to consider financial results.",
                "category": "",
                "date": "01-Apr-2026",
            },
        ]
        self.assertEqual(extract_next_results_date(filings), "2026-05-15")


class ClassifyFilingsTest(unittest.TestCase):
    def test_combines_all_three_classifiers(self) -> None:
        filings = [
            {"title": "Dividend declared", "desc": "", "category": "", "date": "01-Jan-2026"},
            {"title": "CRISIL upgrades rating", "desc": "from 'A' to 'A+'.", "category": "", "date": "02-Jan-2026"},
            {
                "title": "Board Meeting Intimation",
                "desc": "Meeting on 01/03/2026 to consider financial results.",
                "category": "",
                "date": "03-Jan-2026",
            },
        ]
        result = classify_filings(filings)
        self.assertEqual(len(result["corporate_actions"]), 1)
        self.assertEqual(result["rating_action"]["agency"], "Crisil")
        self.assertEqual(result["next_results_date"], "2026-03-01")

    def test_empty_filings_returns_empty_shape(self) -> None:
        result = classify_filings([])
        self.assertEqual(result, {"corporate_actions": [], "rating_action": None, "next_results_date": None})


if __name__ == "__main__":
    unittest.main()
