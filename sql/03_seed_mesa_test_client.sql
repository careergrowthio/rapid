-- ============================================================
-- RAPID — threshold fix + Mesa test client seed
-- Run this in the SQL Editor of your REBUILD project, after the schema.
-- ============================================================

-- ---- PART A: align thresholds to the LOCKED tiers ----------------------
-- Only needed because you ran the schema before we locked the tiers.
-- Locked: email floor = 70 (nothing below is emailed),
--         strong cutoff = 85 ("Strong fit" vs "Possible fit" label).
alter table match_profiles rename column send_threshold    to strong_threshold;
alter table match_profiles rename column possible_threshold to email_floor;
alter table match_profiles alter column strong_threshold set default 85;
alter table match_profiles alter column email_floor    set default 70;

-- ---- PART B: seed the Mesa test client (our hard case) -----------------
insert into clients (id, name, email, subscription_tier, status, engagement_start)
values ('00000000-0000-0000-0000-000000000001',
        'Mesa Test Client', 'mesa.test@careergrowth.io',
        'Guided', 'rapid_active', current_date)
on conflict (id) do nothing;

insert into match_profiles (
  client_id, seniority_summary, scope, industries, job_titles,
  title_search_queries, skills, preferred_locations, work_arrangements,
  salary_min, salary_max, salary_floor_hard, deal_breakers, geography_constraint,
  email_floor, strong_threshold
) values (
  '00000000-0000-0000-0000-000000000001',
  'Senior sales executive with 20+ years of progressive leadership across SVP and Director-level roles, accountable for multi-region national accounts, P&L management, and strategic revenue growth in the CPG food & beverage sector, including leadership of remote national teams and portfolios in the $150M-$500M revenue range.',
  'National account management and GTM strategy for branded and private-label CPG, P&L optimization, revenue growth across US/Canada, digital transformation enabling real-time analytics across 1,300 retail locations, and multi-site retail operations with cross-functional collaboration.',
  array['CPG food & beverage','consumer packaged goods','retail distribution (mass retailers, club stores)','private-label manufacturing'],
  array['Senior Vice President of Sales','SVP, Sales','Senior Vice President, National Accounts','SVP, National Accounts','Head of National Accounts','Vice President of Sales','Vice President, National Accounts','Senior Director of Sales, Grocery','Senior Director, National Accounts','Director of Sales and National Account Management','Director of National Accounts','Director of National Account Management','National Accounts Director','NAM Director','Grocery Sales Director'],
  array['Senior:* Vice:* President:* Sales:*','SVP:* Sales:*','Senior:* Vice:* President:* National:* Account:*','SVP:* National:* Account:*','Vice:* President:* Sales:*','VP:* Sales:*','Vice:* President:* National:* Account:*','VP:* National:* Account:*','Head:* National:* Account:*','Head:* National:* Accounts:*','Head:* Sales:* National:* Account:*','Senior:* Director:* Sales:*','Director:* Sales:*','Senior:* Director:* National:* Account:*','Director:* National:* Account:*','Director:* National:* Account:* Management:*','National:* Account:* Director:*','NAM:* Director:*','NAM:* Manager:*','National:* Account:* Manager:*','National:* Account:* Management:*','Director:* Sales:* National:* Account:*','Sales:* Director:* National:* Account:*','Grocery:* Sales:* Director:*','Senior:* Director:* Grocery:* Sales:*','Grocery:* Account:* Director:*','Grocery:* Account:* Manager:*','Key:* Account:* Director:*','Key:* Account:* Manager:*','Strategic:* Accounts:* Director:*','Enterprise:* Accounts:* Director:*','Senior:* Director:* National:* Accounts:*','Accounts:* Director:* National:*','Sales:* Leader:* National:* Account:*'],
  array['Analyzing data to drive decisions','Building cross-functional relationships','Coaching or mentoring others','CRM systems (e.g., Salesforce, HubSpot)','Data analysis and visualization tools (e.g., Excel, Tableau, Power BI)','Designing customer experience strategies','Developing or launching products/services','Driving culture change or transformation','Driving revenue growth / business development','Ensuring compliance or risk management','Managing supply chains or logistics','Owning a budget or financial targets','Planning and strategy','Presenting to senior leaders or executives','Project management tools (e.g., Jira, Asana, Trello)','Resolving conflicts','Using AI tools or automation in your work'],
  array['Mesa, Arizona'],
  array['Remote'],
  125000, 175000, 125000,
  array['relocation'],
  'US_only',
  70, 85
)
on conflict (client_id) do nothing;

insert into client_memory (client_id, memory)
values ('00000000-0000-0000-0000-000000000001',
        'Seed note: senior CPG national-accounts sales exec, remote-only + no relocation -> very narrow targeting. Use this client to test over-narrow / zero-match handling and the feasibility check.')
on conflict (client_id) do nothing;

-- ---- To reset this test client later -----------------------------------
-- delete from clients where id = '00000000-0000-0000-0000-000000000001';
-- (cascades to match_profiles, client_memory, jobs, matches, feedback)
