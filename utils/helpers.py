import pandas as pd
from datetime import date
from utils.supabase_client import get_supabase
import streamlit as st


@st.cache_data(ttl=300)
def get_users_list() -> list[dict]:
    """Returns all active profiles for dropdown menus."""
    sb = get_supabase()
    res = sb.table("profiles").select("id, full_name, email, role, department_id").eq("is_active", True).order("full_name").execute()
    return res.data or []


@st.cache_data(ttl=3600)
def get_departments() -> list[dict]:
    sb = get_supabase()
    res = sb.table("departments").select("*").order("name").execute()
    return res.data or []


def users_options(include_blank: bool = True) -> dict:
    """Returns {display_name: id} dict for st.selectbox."""
    users = get_users_list()
    opts = {}
    if include_blank:
        opts["— Select owner —"] = None
    for u in users:
        opts[f"{u['full_name']} ({u['email']})"] = u["id"]
    return opts


def status_badge(status: str) -> str:
    colours = {
        "open":          "🟡",
        "in_progress":   "🔵",
        "closed":        "🟢",
        "overdue":       "🔴",
        "current":       "🟢",
        "due_for_review":"🟡",
        "scheduled":     "🟡",
        "completed":     "🟢",
        "cancelled":     "⚪",
        "superseded":    "⚫",
    }
    return f"{colours.get(status,'⚪')} {status.replace('_',' ').title()}"


def days_until(d) -> int | None:
    if d is None:
        return None
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return (d - date.today()).days


def overdue_banner():
    """Show a red banner if there are overdue items. Call on every page."""
    sb = get_supabase()
    nc_res  = sb.table("v_overdue_nc").select("id", count="exact").execute()
    doc_res = sb.table("v_overdue_docs").select("id", count="exact").execute()
    nc_count  = nc_res.count  or 0
    doc_count = doc_res.count or 0
    total = nc_count + doc_count
    if total > 0:
        parts = []
        if nc_count:  parts.append(f"**{nc_count} overdue NC/CAPA finding{'s' if nc_count>1 else ''}**")
        if doc_count: parts.append(f"**{doc_count} document{'s' if doc_count>1 else ''} overdue for review**")
        st.error(f"⚠️ Overdue items: {' · '.join(parts)}")


def format_date(d) -> str:
    if not d:
        return "—"
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime("%d %b %Y")
