from marketvector_auth import require_login as _mv_require_login
_mv_require_login()

from client_actionable_dashboard import render_client_dashboard
render_client_dashboard("TLT", "WEEKLY")
