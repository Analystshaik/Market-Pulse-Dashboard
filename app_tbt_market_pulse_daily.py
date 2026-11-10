"""Protected fixed-reference Market Pulse Daily dashboard. MARKETVECTOR_HOME_PULSE_AND_COLUMNS_V1_0."""
from marketvector_auth import require_login as _require_login
_require_login()

from market_pulse_dashboard import render_market_pulse_dashboard
render_market_pulse_dashboard(
    reference_override="TBT",
    timeframe_override="DAILY",
    home_mode=True,
)
