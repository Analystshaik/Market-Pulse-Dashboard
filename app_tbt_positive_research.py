"""Protected research-only entrypoint, also used by the Market Pulse navigation."""
from marketvector_auth import require_login as _mv_require_login
_mv_require_login()

from tbt_positive_research_dashboard import render_tbt_positive_research
render_tbt_positive_research()
