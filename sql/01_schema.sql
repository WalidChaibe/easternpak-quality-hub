-- ============================================================
-- EASTERNPAK QUALITY HUB — Supabase Schema
-- Run this in Supabase SQL Editor (in order)
-- ============================================================

-- ── EXTENSIONS ──────────────────────────────────────────────
create extension if not exists "uuid-ossp";

-- ── DEPARTMENTS ─────────────────────────────────────────────
create table departments (
  id   serial primary key,
  name text not null unique
);

insert into departments (name) values
  ('Quality'),
  ('Production'),
  ('Maintenance'),
  ('Technical'),
  ('Planning'),
  ('Customer Service'),
  ('RM Warehouse'),
  ('FG Warehouse'),
  ('Purchasing'),
  ('Artwork');

-- ── USERS (extends Supabase auth.users) ─────────────────────
create table profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  full_name     text not null,
  email         text not null unique,
  role          text not null check (role in ('admin','quality_manager','quality_engineer','viewer')),
  department_id integer references departments(id),
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

-- Auto-create profile row when a new auth user is created
create or replace function handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into profiles (id, full_name, email, role)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'full_name', new.email),
    new.email,
    coalesce(new.raw_user_meta_data->>'role', 'viewer')
  );
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- ── NC / CAPA FINDINGS ──────────────────────────────────────
create table nc_findings (
  id                  uuid primary key default uuid_generate_v4(),
  audit_type          text not null check (audit_type in ('ISO9001','BRCGS')),
  audit_ref           text,                        -- e.g. "ISO Audit 2024", "BRCGS Annual 2024"
  finding_ref         text,                        -- e.g. "NC-001"
  clause_ref          text,                        -- ISO clause or BRCGS requirement ref
  details             text not null,               -- Details of non-conformity
  root_cause          text,
  correction          text,                        -- Immediate correction taken
  preventive_action   text,                        -- Preventive action plan
  action_owner_id     uuid references profiles(id),
  target_date         date,
  closing_date        date,
  status              text not null default 'open'
                        check (status in ('open','in_progress','closed','overdue')),
  evidence_notes      text,
  created_by          uuid references profiles(id),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- Auto-update updated_at
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end;
$$;

create trigger nc_findings_updated_at
  before update on nc_findings
  for each row execute function set_updated_at();

-- ── KPI DEFINITIONS ─────────────────────────────────────────
create table kpi_definitions (
  id            serial primary key,
  name          text not null,
  department_id integer not null references departments(id),
  unit          text,                  -- e.g. '%', 'days', 'count'
  target_value  numeric,
  target_type   text check (target_type in ('>=','<=','=')),
  audit_type    text check (audit_type in ('ISO9001','BRCGS','Both')),
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

-- ── KPI MONTHLY ENTRIES ─────────────────────────────────────
create table kpi_entries (
  id                  uuid primary key default uuid_generate_v4(),
  kpi_id              integer not null references kpi_definitions(id),
  month               date not null,              -- store as first day of month e.g. 2025-03-01
  actual_value        numeric,
  achieved            boolean,                    -- auto-computed but overridable
  root_cause          text,                       -- required if not achieved
  corrective_action   text,                       -- required if not achieved
  entered_by          uuid references profiles(id),
  created_at          timestamptz not null default now(),
  unique (kpi_id, month)
);

-- ── REQUIRED DOCUMENTATION ──────────────────────────────────
create table documents (
  id                uuid primary key default uuid_generate_v4(),
  title             text not null,
  doc_code          text,                         -- e.g. "QP-001"
  audit_type        text not null check (audit_type in ('ISO9001','BRCGS','Both')),
  doc_type          text,                         -- e.g. 'Risk Assessment', 'Training Record', 'MoM'
  owner_id          uuid references profiles(id),
  review_frequency  text check (review_frequency in ('Monthly','Quarterly','Bi-Annual','Annual')),
  last_reviewed     date,
  next_review_due   date,
  location          text,                         -- file path or SharePoint link
  version           text,
  status            text not null default 'current'
                      check (status in ('current','due_for_review','overdue','superseded')),
  notes             text,
  created_by        uuid references profiles(id),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create trigger documents_updated_at
  before update on documents
  for each row execute function set_updated_at();

-- ── INTERNAL AUDITS ─────────────────────────────────────────
create table audits (
  id            uuid primary key default uuid_generate_v4(),
  audit_type    text not null check (audit_type in ('ISO9001','BRCGS')),
  audit_name    text not null,
  scheduled_date date,
  conducted_date date,
  lead_auditor_id uuid references profiles(id),
  scope         text,
  status        text not null default 'scheduled'
                  check (status in ('scheduled','in_progress','completed','cancelled')),
  summary       text,
  created_by    uuid references profiles(id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger audits_updated_at
  before update on audits
  for each row execute function set_updated_at();

-- Link nc_findings to an audit
alter table nc_findings add column audit_id uuid references audits(id);

-- ── ROW LEVEL SECURITY ──────────────────────────────────────
alter table profiles      enable row level security;
alter table nc_findings   enable row level security;
alter table kpi_definitions enable row level security;
alter table kpi_entries   enable row level security;
alter table documents     enable row level security;
alter table audits        enable row level security;

-- Profiles: users can read all, only admins can write
create policy "profiles_read_all"  on profiles for select using (true);
create policy "profiles_admin_write" on profiles for all
  using (
    exists (select 1 from profiles p where p.id = auth.uid() and p.role = 'admin')
  );

-- All authenticated users can read everything
create policy "nc_read"  on nc_findings   for select using (auth.role() = 'authenticated');
create policy "kpid_read" on kpi_definitions for select using (auth.role() = 'authenticated');
create policy "kpie_read" on kpi_entries   for select using (auth.role() = 'authenticated');
create policy "docs_read" on documents     for select using (auth.role() = 'authenticated');
create policy "audits_read" on audits      for select using (auth.role() = 'authenticated');

-- Write: admin + quality_manager + quality_engineer (not viewer)
create policy "nc_write" on nc_findings for all
  using (exists (select 1 from profiles p where p.id = auth.uid() and p.role in ('admin','quality_manager','quality_engineer')));

create policy "kpie_write" on kpi_entries for all
  using (exists (select 1 from profiles p where p.id = auth.uid() and p.role in ('admin','quality_manager','quality_engineer')));

create policy "docs_write" on documents for all
  using (exists (select 1 from profiles p where p.id = auth.uid() and p.role in ('admin','quality_manager','quality_engineer')));

create policy "audits_write" on audits for all
  using (exists (select 1 from profiles p where p.id = auth.uid() and p.role in ('admin','quality_manager','quality_engineer')));

-- ── USEFUL VIEWS ────────────────────────────────────────────

-- Overdue NC findings (target date passed, not closed)
create or replace view v_overdue_nc as
select f.*, p.full_name as owner_name, p.email as owner_email
from nc_findings f
left join profiles p on f.action_owner_id = p.id
where f.status != 'closed'
  and f.target_date < current_date;

-- KPI entries with definition details
create or replace view v_kpi_full as
select
  e.*,
  d.name        as kpi_name,
  d.unit        as kpi_unit,
  d.target_value,
  d.target_type,
  d.audit_type  as kpi_audit_type,
  dep.name      as department_name,
  p.full_name   as entered_by_name
from kpi_entries e
join kpi_definitions d  on e.kpi_id = d.id
join departments dep     on d.department_id = dep.id
left join profiles p     on e.entered_by = p.id;

-- Documents overdue for review
create or replace view v_overdue_docs as
select d.*, p.full_name as owner_name
from documents d
left join profiles p on d.owner_id = p.id
where d.next_review_due < current_date
  and d.status != 'superseded';
