# Rapid — Match Email Template

Built from the existing production email (the Michael Olson send), kept in the same
voice and structure. Two variants: **Strong fit** (apply-now urgency) and **Possible
fit** (worth-a-look, honest framing). The dev wires this into `send_match_email()`
via Postmark (Transactional stream).

From: `RAPID MATCH <rapidnotifications@careergrowth.io>`

---

## Placeholders (engine field -> template)

| Placeholder | Source |
|---|---|
| `{{CLIENT_NAME}}` | clients.name (full, upper-cased in subject/header as today) |
| `{{JOB_TITLE}}` | jobs.job_title |
| `{{COMPANY}}` | jobs.company |
| `{{LOCATION}}` | jobs.location (or "Remote" when is_remote) |
| `{{SALARY}}` | jobs.salary_range (omit the 💰 line entirely if absent) |
| `{{SCORE}}` | matches.composite_score |
| `{{RESUME_LINK}}` | matches.resume_url |
| `{{APPLY_LINK}}` | jobs.job_url |
| `{{WHY}}` | matches.match_reason (the matching skill's overall_assessment) |
| `{{N}}` / `{{MATCH_WORD}}` | sent count this email; "match" if 1, "matches" if more |

---

## Variant A — Strong fit (>= strong_threshold)

**Subject:** `NEW: RAPID JOB MATCH FOR {{CLIENT_NAME}}`

🎯 JOB MATCHES FOR {{CLIENT_NAME}} 🎯

🥇 **{{JOB_TITLE}}** at {{COMPANY}}
{{LOCATION}} | 💰 {{SALARY}} PER YEAR
Match Score: {{SCORE}}% — STRONG FIT
📄 Tailored Resume: [View Resume]({{RESUME_LINK}})
[Apply Here]({{APPLY_LINK}})

**Why it matches:** {{WHY}}

💡 Found {{N}} quality {{MATCH_WORD}} (70%+ fit). Strong fits go fast — apply within 24 hours for best results!

---

## Variant B — Possible fit (70-84%)

**Subject:** `NEW: RAPID JOB MATCH FOR {{CLIENT_NAME}}`

🎯 JOB MATCHES FOR {{CLIENT_NAME}} 🎯

🥈 **{{JOB_TITLE}}** at {{COMPANY}}
{{LOCATION}} | 💰 {{SALARY}} PER YEAR
Match Score: {{SCORE}}% — POSSIBLE FIT
📄 Tailored Resume: [View Resume]({{RESUME_LINK}})
[Apply Here]({{APPLY_LINK}})

**Why it's worth a look:** {{WHY}}

💡 This one cleared our 70% quality bar but isn't a slam dunk — you decide. Your tailored resume is ready either way.

---

## Rules for the dev

- One email per match (as today), sent at delivery time; respect the 3/day cap.
- The 🥇/🥈 medal, the tier label on the score line, and the closing line are the
  only differences between variants — everything else is identical.
- `{{WHY}}` is used verbatim from the matching skill's overall_assessment, which is
  already written in second person ("You should apply...") in the current system's voice.
- Omit the salary line when the posting lists none; never show "null".
- Pluralize correctly: "Found 1 quality match", "Found 2 quality matches".
- Plain, high-deliverability HTML (no heavy images); Postmark Transactional stream.
