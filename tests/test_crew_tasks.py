"""Tests for config/crew_tasks.py's analyst prompt construction.

filings is fetched as one of the six ALL_DATA_TASKS (main.py, crew.ALL_DATA_TASKS)
and flows all the way to the frontend report (see test_main_report.py), but was
never actually included in the analyst LLM prompt — ANALYST_SECTIONS (sourced
from config/analyst.json's "sections" map) simply didn't have a "filings" entry,
so recent corporate announcements/filings data was silently dropped before ever
reaching the model, despite being fetched and cached on every run.
"""
import unittest

from config.crew_tasks import ANALYST_SECTIONS, build_analysis_prompt


class FilingsInAnalystPromptTest(unittest.TestCase):
    def test_filings_section_is_registered(self) -> None:
        self.assertIn("filings", ANALYST_SECTIONS)

    def test_filings_data_appears_in_the_built_prompt(self) -> None:
        all_data = {
            "filings": {
                "filings": [
                    {
                        "title": "Board approves preferential allotment",
                        "desc": "The board approved raising funds via preferential allotment.",
                        "date": "2026-07-20",
                        "category": "Board Meeting",
                        "attachment": "https://nse.example/x.pdf",
                    }
                ]
            },
        }
        prompt = build_analysis_prompt("TCS", all_data)
        self.assertIn("Board approves preferential allotment", prompt)

    def test_missing_filings_task_does_not_break_prompt_construction(self) -> None:
        # No "filings" key in all_data at all (e.g. the fetch failed and was
        # dropped) must not raise — same "absent, not guessed" convention as
        # every other section.
        prompt = build_analysis_prompt("TCS", {})
        self.assertIsInstance(prompt, str)


if __name__ == "__main__":
    unittest.main()
