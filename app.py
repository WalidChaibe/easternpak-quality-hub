import streamlit as st
from utils.auth import login, logout, get_profile, get_role, ROLE_LABELS, has_permission
from utils.helpers import overdue_banner

st.set_page_config(
    page_title="Easternpak Quality Hub",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stSidebar"] { background: #0f1c2e; }
[data-testid="stSidebarNav"] { display: none; }
[data-testid="stSidebar"] * { color: #d4dbe8 !important; }
[data-testid="stSidebar"] .stButton button {
    background: transparent;
    border: 1px solid #2a3f5f;
    color: #d4dbe8 !important;
    width: 100%;
    text-align: left;
    margin-bottom: 2px;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: #1a2e4a;
    border-color: #4a7fc1;
}
</style>
""", unsafe_allow_html=True)


# ── Login screen ──────────────────────────────────────────────
def show_login():
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("## 🏭 Easternpak Quality Hub")
        st.markdown("**NapcoNational · Easternpak Division**")
        st.markdown("---")
        with st.form("login_form"):
            email    = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log In", use_container_width=True)
        if submitted:
            with st.spinner("Signing in…"):
                if login(email, password):
                    st.rerun()
                else:
                    st.error("Invalid email or password.")


# ── Navigation items: (page key, label, permission key) ───────
NAV_ITEMS = [
    ("dashboard",    "🏠  Dashboard",               "dashboard"),
    ("nc",           "📋  NC / CAPA",               "nc"),
    ("kpi",          "📊  KPI Tracking",             "kpi"),
    ("requirements", "📘  Requirements Register",    "requirements"),
    ("documents",    "📁  Document Register",        "documents"),
    ("audits",       "🔍  Internal Audits",          "audits"),
    ("proc_builder", "📄  Procedures & Processes",   "proc_builder"),
    ("esko",         "⏱️  Esko Lead Time",           "esko"),
    ("forecast",     "📈  Demand Forecast",          "forecast"),
    ("admin",        "⚙️  User Management",          "admin"),
]


def _perm_for(page: str) -> str:
    """Permission key for a page key (the NC sub-pages share the 'nc' permission)."""
    return "nc" if page in ("nc", "nc_iso", "nc_brcgs") else page


# ── Sidebar navigation ────────────────────────────────────────
def show_sidebar():
    profile = get_profile()
    role    = get_role()

    with st.sidebar:
        st.markdown("### 🏭 Quality Hub")
        st.markdown(f"**{profile['full_name']}**")
        st.caption(ROLE_LABELS.get(role, role))
        st.markdown("---")

        # Land on the first page this role may open (a forecast-only user never sees the dashboard)
        allowed = [key for key, _, perm in NAV_ITEMS if has_permission(perm)]
        if allowed and _perm_for(st.session_state.get("page", "")) not in allowed:
            st.session_state["page"] = allowed[0]

        for key, label, perm in NAV_ITEMS:
            if has_permission(perm):
                active = "▶ " if st.session_state.get("page") == key else "   "
                if st.button(f"{active}{label}", key=f"nav_{key}"):
                    st.session_state["page"] = key
                    st.rerun()

        st.markdown("---")
        if st.button("🚪  Log Out"):
            logout()


# ── Page router ───────────────────────────────────────────────
def route():
    page = st.session_state.get("page", "dashboard")

    # Server-side guard: never render a page the role isn't allowed to open
    if not has_permission(_perm_for(page)):
        st.error("You don't have access to this page.")
        st.stop()

    if page == "dashboard":
        from pages.dashboard    import show; show()
    elif page in ("nc", "nc_iso", "nc_brcgs"):
        st.session_state["page"] = "nc"
        from pages.nc_findings  import show; show()
    elif page == "kpi":
        from pages.kpi          import show; show()
    elif page == "requirements":
        from pages.requirements import show; show()
    elif page == "documents":
        from pages.documents    import show; show()
    elif page == "audits":
        from pages.audits       import show; show()
    elif page == "proc_builder":
        from pages.proc_builder import show; show()
    elif page == "esko":
        from pages.esko_lead_time import show; show()
    elif page == "forecast":
        from pages.demand_forecast import show; show()
    elif page == "admin":
        from pages.admin        import show; show()


# ── Entry point ───────────────────────────────────────────────
if "profile" not in st.session_state:
    show_login()
else:
    show_sidebar()
    if has_permission("dashboard"):   # NC / document alerts only for quality roles
        overdue_banner()
    route()