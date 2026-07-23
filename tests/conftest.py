"""Force the real tools.* modules into sys.modules before pytest collects any
test file.

test_analysis_guardrails.py uses sys.modules.setdefault(...) to stub out
tools.nse_tools / tools.screener_tools / tools.news_tools so it can import
crew.py without pulling in crewai's real (heavy) tool machinery. setdefault()
only takes effect if the name isn't already cached — but pytest imports every
test module during collection before running any of them, so whichever test
file gets collected first otherwise decides, for the rest of the process,
whether these names resolve to the real modules or that file's stubs. Importing
them here first, in this directory-wide conftest.py (always loaded before any
test module), guarantees test files that need the real implementations (e.g.
test_nse_tools.py, test_screener_tools.py) always get them.
"""
import tools.news_tools  # noqa: F401
import tools.nse_tools  # noqa: F401
import tools.screener_tools  # noqa: F401
