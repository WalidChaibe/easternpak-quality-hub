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


# ── Sidebar navigation ────────────────────────────────────────
def show_sidebar():
    profile = get_profile()
    role    = get_role()

    with st.sidebar:
        st.markdown("### 🏭 Quality Hub")
        st.markdown(f"**{profile['full_name']}**")
        st.caption(ROLE_LABELS.get(role, role))
        st.markdown("---")

        nav_items = [
            ("dashboard",  "🏠  Dashboard",              "dashboard"),
            ("nc_iso",     "📋  NC/CAPA — ISO 9001",     "nc_iso"),
            ("nc_brcgs",   "📋  NC/CAPA — BRCGS",        "nc_brcgs"),
            ("kpi",        "📊  KPI Tracking",            "kpi"),
            ("documents",  "📁  Document Register",       "documents"),
            ("audits",     "🔍  Internal Audits",         "audits"),
            ("admin",      "⚙️  User Management",         "admin"),
        ]

        if "page" not in st.session_state:
            st.session_state["page"] = "dashboard"

        for key, label, perm in nav_items:
            if has_permission(perm):
                active = "▶ " if st.session_state["page"] == key else "   "
                if st.button(f"{active}{label}", key=f"nav_{key}"):
                    st.session_state["page"] = key
                    st.rerun()

        st.markdown("---")
        if st.button("🚪  Log Out"):
            logout()


# ── Page router ───────────────────────────────────────────────
def route():
    page = st.session_state.get("page", "dashboard")

    if page == "dashboard":
        from pages.dashboard    import show; show()
    elif page == "nc_iso":
        from pages.nc_capa      import show; show("ISO9001")
    elif page == "nc_brcgs":
        from pages.nc_capa      import show; show("BRCGS")
    elif page == "kpi":
        from pages.kpi          import show; show()
    elif page == "documents":
        from pages.documents    import show; show()
    elif page == "audits":
        from pages.audits       import show; show()
    elif page == "admin":
        from pages.admin        import show; show()


# ── Entry point ───────────────────────────────────────────────
if "profile" not in st.session_state:
    show_login()
else:
    show_sidebar()
    overdue_banner()
    route()
