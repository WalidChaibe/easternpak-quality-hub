import os
import requests
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
        url = os.environ.get("SUPABASE_URL") or st.secrets["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_ANON_KEY") or st.secrets["SUPABASE_ANON_KEY"]

        # Strip trailing slash if present
        url = url.rstrip("/")

        # Sign in via Supabase Auth REST API
        res = requests.post(
            f"{url}/auth/v1/token?grant_type=password",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"email": email, "password": password},
            timeout=10,
        )

        if res.status_code != 200:
            err = res.json()
            st.error(f"Login failed ({res.status_code}): {err.get('error_description') or err.get('msg') or res.text}")
            return False

        data         = res.json()
        access_token = data["access_token"]
        user_id      = data["user"]["id"]

        # Fetch profile
        sb      = get_supabase()
        profile = sb.table("profiles").select("*").eq("id", user_id).single().execute()

        st.session_state["user"]         = data["user"]
        st.session_state["access_token"] = access_token
        st.session_state["profile"]      = profile.data
        return True

    except Exception as e:
        st.error(f"Login error: {e}")
    return False


def logout():
    for k in ["user", "access_token", "profile", "session"]:
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
