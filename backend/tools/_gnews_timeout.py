"""Defense-in-depth network timeout for every GNews call site.

gnews's GNews.get_news() delegates to feedparser.parse(url, agent=...),
which uses urllib under the hood with no per-call timeout parameter of its
own — a slow/unresponsive Google News response can hang the calling thread
indefinitely. market_picks_pipeline.py's _phase_scrape runs each source
scraper inside a ThreadPoolExecutor, whose `with` block waits (shutdown
with wait=True) for every submitted thread to finish, so one hung GNews
call there would hang the entire weekly pipeline run, not just that one
source.

socket.setdefaulttimeout() only ever provides the *default* used when a
socket is opened without its own explicit timeout — every `requests`-based
call elsewhere in this codebase already passes its own explicit `timeout=`
(which always takes priority over this), so setting this only ever affects
call paths — like GNews's — that currently have none.

Importing this module is the side effect; every module that calls GNews
does `import tools._gnews_timeout  # noqa: F401` before using it. The
module body only runs once (Python caches it in sys.modules), so importing
it from several call sites is safe and idempotent.
"""

import socket

GNEWS_SOCKET_TIMEOUT_SECONDS = 20

socket.setdefaulttimeout(GNEWS_SOCKET_TIMEOUT_SECONDS)
