import streamlit as st
from utils.supabase_client import get_supabase

ROLE_LABELS = {
    "admin":           "Admin",
    "quality_manager": "Quality Manager",
    "quality_engineer":"Quality Engineer",
    "viewer":          "Viewer",
}

ROLE_PERMISSIONS = {
    "admin":           ["dashboard","nc_iso","nc_brcgs","kpi","documents","audits","admin"],
    "quality_manager": ["dashboard","nc_iso","nc_brcgs","kpi","documents","audits"],
    "quality_engineer":["dashboard","nc_iso","nc_brcgs","kpi","documents","audits"],
    "viewer":          ["dashboard","nc_iso","nc_brcgs","kpi","documents","audits"],
}

WRITE_ROLES = {"admin","quality_manager","quality_engineer"}


def login(email: str, password: str) -> bool:
    sb = get_supabase()
    try:
        res = sb.auth.sign_in_with_password({"email": email, "password": password})
        if res.user:
            profile = sb.table("profiles").select("*").eq("id", res.user.id).single().execute()
            st.session_state["user"]    = res.user
            st.session_state["session"] = res.session
            st.session_state["profile"] = profile.data
            return True
    except Exception as e:
        st.error(f"Login failed: {e}")
    return False


def logout():
    sb = get_supabase()
    sb.auth.sign_out()
    for key in ["user","session","profile"]:
        st.session_state.pop(key, None)
    st.rerun()


def get_profile() -> dict | None:
    return st.session_state.get("profile")


def get_role() -> str | None:
    p = get_profile()
    return p["role"] if p else None


def can_write() -> bool:
    return get_role() in WRITE_ROLES


def require_auth():
    """Call at top of every page. Redirects to login if not authenticated."""
    if "profile" not in st.session_state:
        st.warning("Please log in to access this page.")
        st.stop()


def has_permission(page_key: str) -> bool:
    role = get_role()
    if not role:
        return False
    return page_key in ROLE_PERMISSIONS.get(role, [])
