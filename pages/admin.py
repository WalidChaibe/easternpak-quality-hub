import os
import streamlit as st
import pandas as pd
from supabase import create_client
from utils.auth import require_auth, get_role
from utils.supabase_client import get_supabase
from utils.helpers import get_departments


def get_admin_client():
    """Service role client — bypasses RLS, used only for user management."""
    url = os.environ.get("SUPABASE_URL") or st.secrets["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or st.secrets.get("SUPABASE_SERVICE_KEY")
    if not key:
        st.error("SUPABASE_SERVICE_KEY not set in secrets. Please add it in Streamlit settings.")
        st.stop()
    return create_client(url, key)


def show():
    require_auth()

    if get_role() != "admin":
        st.error("⛔ Access denied. Admin role required.")
        st.stop()

    st.title("⚙️ User Management")
    st.caption("Add users, assign roles and departments.")

    sb = get_supabase()

    tab_list, tab_add = st.tabs(["👥 All Users", "➕ Add User"])

    with tab_list:
        res = sb.table("profiles").select("*, departments(name)").order("full_name").execute()
        users = res.data or []

        if not users:
            st.info("No users found.")
        else:
            rows = []
            for u in users:
                rows.append({
                    "Name":       u.get("full_name", ""),
                    "Email":      u.get("email", ""),
                    "Role":       u.get("role", ""),
                    "Department": (u.get("departments") or {}).get("name", "—"),
                    "Active":     "✓" if u.get("is_active") else "✗",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_add:
        st.markdown("#### Add New User")
        st.info("This will create the user directly. They can log in immediately with the password you set.")

        depts = get_departments()
        dept_map = {"— No department —": None, **{d["name"]: d["id"] for d in depts}}

        with st.form("add_user", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                full_name = st.text_input("Full Name *")
                email     = st.text_input("Email Address *")
                password  = st.text_input("Password *", type="password")
            with c2:
                role = st.selectbox("Role *", ["viewer", "quality_engineer", "quality_manager", "admin"])
                dept = st.selectbox("Department", list(dept_map.keys()))

            submitted = st.form_submit_button("➕ Create User")

        if submitted:
            if not full_name or not email or not password:
                st.error("Name, email and password are all required.")
            elif len(password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                try:
                    # Insert directly into profiles with hashed password
                    import uuid
                    new_id = str(uuid.uuid4())

                    sb.table("profiles").insert({
                        "id":            new_id,
                        "full_name":     full_name,
                        "email":         email,
                        "role":          role,
                        "department_id": dept_map[dept],
                        "is_active":     True,
                        "password_hash": sb.rpc("hash_password", {"p_password": password}).execute().data,
                    }).execute()

                    st.success(f"✅ User '{full_name}' created successfully. They can now log in.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error creating user: {e}")
