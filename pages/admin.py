import streamlit as st
import pandas as pd
from utils.auth import require_auth, get_role
from utils.supabase_client import get_supabase
from utils.helpers import get_departments


def show():
    require_auth()

    if get_role() != "admin":
        st.error("⛔ Access denied. Admin role required.")
        st.stop()

    st.title("⚙️ User Management")
    st.caption("Add users, assign roles and departments. Users receive a Supabase invite email.")

    sb = get_supabase()

    tab_list, tab_add = st.tabs(["👥 All Users", "➕ Invite User"])

    with tab_list:
        res = sb.table("profiles").select("*, departments(name)").order("full_name").execute()
        users = res.data or []

        if not users:
            st.info("No users found.")
        else:
            rows = []
            for u in users:
                rows.append({
                    "Name":       u.get("full_name",""),
                    "Email":      u.get("email",""),
                    "Role":       u.get("role",""),
                    "Department": (u.get("departments") or {}).get("name","—"),
                    "Active":     "✓" if u.get("is_active") else "✗",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_add:
        st.markdown("#### Invite New User")
        st.info("This will create a Supabase auth user and send them an invitation email. They set their own password on first login.")

        depts = get_departments()
        dept_map = {"— No department —": None, **{d["name"]: d["id"] for d in depts}}

        with st.form("invite_user", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                full_name = st.text_input("Full Name *")
                email     = st.text_input("Email Address *")
            with c2:
                role = st.selectbox("Role *", ["viewer","quality_engineer","quality_manager","admin"])
                dept = st.selectbox("Department", list(dept_map.keys()))

            submitted = st.form_submit_button("📧 Send Invitation")

        if submitted:
            if not full_name or not email:
                st.error("Name and email are required.")
            else:
                try:
                    # Supabase admin invite (requires service role key for production)
                    # For now, create user directly
                    import secrets, string
                    temp_pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

                    res = sb.auth.admin.invite_user_by_email(email, {
                        "data": {
                            "full_name": full_name,
                            "role": role,
                        }
                    })

                    # Update profile with department
                    if res.user:
                        sb.table("profiles").update({
                            "full_name": full_name,
                            "role": role,
                            "department_id": dept_map[dept],
                        }).eq("id", res.user.id).execute()

                    st.success(f"✅ Invitation sent to {email}. They will receive an email to set their password.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error inviting user: {e}")
                    st.caption("Note: User invitation requires Supabase service role key. Ensure SUPABASE_SERVICE_KEY is set.")
