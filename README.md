# Easternpak Quality Hub
**NapcoNational · Easternpak Division**

A quality management web application covering ISO 9001 and BRCGS requirements.

---

## Modules
| Module | Description |
|--------|-------------|
| Dashboard | Live overdue alerts, KPI miss count, open findings summary |
| NC/CAPA — ISO 9001 | Log, track and close ISO 9001 audit findings |
| NC/CAPA — BRCGS | Log, track and close BRCGS audit findings |
| KPI Tracking | Monthly entry by department, trend charts, Excel export |
| Document Register | Required documentation for both standards, review schedule |
| Internal Audits | Audit schedule and findings linkage |
| User Management | Admin-only: invite users, assign roles and departments |

## Stack
- **Frontend / App**: Streamlit (Python)
- **Database + Auth**: Supabase (PostgreSQL + Row Level Security)
- **Hosting**: Streamlit Community Cloud (free)
- **Source control**: GitHub

---

## Setup Instructions

### 1. Supabase
1. Create a new project at https://supabase.com
2. Go to **SQL Editor** and run `sql/01_schema.sql` in full
3. Go to **Authentication → Settings** and enable email auth
4. Copy your **Project URL** and **anon public key** from Project Settings → API

### 2. Local Development
```bash
git clone https://github.com/YOUR_ORG/easternpak-quality-hub.git
cd easternpak-quality-hub

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edit secrets.toml with your Supabase URL and keys

streamlit run app.py
```

### 3. Create First Admin User
In Supabase dashboard → Authentication → Users → Invite user.
Then in SQL Editor:
```sql
update profiles
set role = 'admin', full_name = 'Your Name'
where email = 'your@email.com';
```

### 4. Deploy to Streamlit Cloud
1. Push code to GitHub (secrets.toml is gitignored — never commit it)
2. Go to https://share.streamlit.io → New app → connect your repo
3. Set main file: `app.py`
4. In **Advanced settings → Secrets**, paste the contents of your secrets.toml

---

## User Roles
| Role | Access |
|------|--------|
| `admin` | Full access + user management |
| `quality_manager` | Full read/write on all modules |
| `quality_engineer` | Full read/write on all modules |
| `viewer` | Read only across all modules |

---

## Project Structure
```
app.py                   # Entry point, login, sidebar routing
pages/
  dashboard.py           # Home + overdue overview
  nc_capa.py             # NC/CAPA (shared for ISO + BRCGS)
  kpi.py                 # KPI monthly entry + trends
  documents.py           # Document register
  audits.py              # Internal audit schedule
  admin.py               # User management (admin only)
utils/
  auth.py                # Login, logout, role helpers
  supabase_client.py     # Supabase connection
  helpers.py             # Shared utilities, overdue banner
sql/
  01_schema.sql          # Full database schema
requirements.txt
```
