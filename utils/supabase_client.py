import os
from supabase import create_client, Client
import streamlit as st

@st.cache_resource
def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL") or st.secrets["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_ANON_KEY") or st.secrets["SUPABASE_ANON_KEY"]
    return create_client(url, key)
