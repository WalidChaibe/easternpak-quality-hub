import os
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
    try:
        sb = get_supabase()

        # Check password directly against profiles table using pgcrypto
        res = sb.rpc("verify_login", {"p_email": email, "p_password": password}).execute()

        if not res.data:
            st.error("Invalid email or password.")
            return False

        profile = res.data[0]
        st.session_state["profile"] = profile
        return True

    except Exception as e:
        st.error(f"Login error: {e}")
    return False


def logout():
    for k in ["profile"]:
        st.session_state.pop(k, None)
    st.rerun()


def get_profile() -> dict | None:
    return st.session_state.get("profile")


def get_role() -> str | None:
    p = get_profile()
    return p["role"] if p else None


def can_write() -> bool:
    return get_role() in WRITE_ROLES


def require_auth():
    if "profile" not in st.session_state:
        st.warning("Please log in to access this page.")
        st.stop()


def has_permission(page_key: str) -> bool:
    role = get_role()
    if not role:
        return False
    return page_key in ROLE_PERMISSIONS.get(role, [])
