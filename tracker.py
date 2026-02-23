"""
SMT CX Intel Tracker — Weekly automation script
Runs every Friday at 07:30 CET via GitHub Actions
"""

import anthropic
import json
import os
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ── CONFIG ──
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = "C06TY9Y59C5"
REPORTS_DIR = "data/reports"
COMPETITORS_FILE = "data/competitors/index.json"
MANIFEST_FILE = "data/reports/manifest.json"

# ── DATE ──
tz = ZoneInfo("Europe/Madrid")
now = datetime.now(tz)
today = now.strftime("%Y-%m-%d")
week_label = f"Semana del {now.strftime('%-d de %B de %Y')}"

# ── LOAD COMPETITORS ──
with open(COMPETITORS_FILE, encoding="utf-8") as f:
    competitors = json.load(f)

# ── LOAD MANIFEST ──
with open(MANIFEST_FILE, encoding="utf-8") as f:
    manifest = json.load(f)

# Check if report for today already exists
existing_dates = [r["date"] for r in manifest["reports"]]
if today in existing_dates:
    print(f"Report for {today} already exists. Skipping.")
    exit(0)

# ── LOAD PREVIOUS REPORT FOR DELTA ──
prev_report = None
if manifest["reports"]:
    latest = sorted(manifest["reports"], key=lambda x: x["date"], reverse=True)[0]
    prev_path = f"{REPORTS_DIR}/{latest['file']}"
    if os.path.exists(prev_path):
        with open(prev_path, encoding="utf-8") as f:
            prev_report = json.load(f)

# ── BUILD COMPETITOR LIST FOR PROMPT ──
comp_list = "\n".join([
    f"- {c['name']} | URL: {c.get('url', 'N/A')} | Blog: {c.get('blog_url', 'N/A')} | Region: {c.get('region', '—')} | Tech: {c.get('tech', '—')}"
    for c in competitors if c.get('tier') != 'INACTIVE'
])

prev_summary = ""
if prev_report:
    prev_summary = "\n\nPREVIOUS WEEK SUMMARY (for delta tracking):\n" + "\n".join([
        f"- {s['competitor']}: {s['level']} — {s['headline']}"
        for s in prev_report.get("summary", [])
    ])

# ── CALL CLAUDE API ──
print(f"Running tracker for {today}...")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

prompt = f"""You are the SMT Tech Partners competitive intelligence tracker. Today is {today} ({week_label}).

Research the following 13 CX competitors for activity in the last 7-14 days. For each competitor:
1. Search for recent news, blog posts, LinkedIn activity, partnerships, hiring, events, or product launches
2. Analyze their blog/website content to understand their CX methodology, AI approach, and delivery model
3. Assess activity level: HIGH (major news/change), MEDIUM (some activity), LOW (minimal/no activity)

COMPETITORS TO RESEARCH:
{comp_list}
{prev_summary}

Return ONLY a valid JSON object with this exact structure:
{{
  "date": "{today}",
  "week_label": "{week_label}",
  "summary": [
    {{
      "competitor": "Name",
      "level": "HIGH|MEDIUM|LOW",
      "headline": "2-3 sentence summary of what was found this week",
      "url": "most relevant URL found (must be real and working)",
      "blog_profile": "3-5 sentence intellectual profile based on blog/content analysis (methodology, AI stance, delivery model, thought leadership themes). Only update if new content found this week, otherwise carry forward previous profile."
    }}
  ],
  "takeaways": [
    "Sales/partnership takeaway 1",
    "Sales/partnership takeaway 2",
    "Sales/partnership takeaway 3",
    "Sales/partnership takeaway 4",
    "Sales/partnership takeaway 5"
  ]
}}

QUALITY RULES:
- Never invent URLs. Only include URLs you have verified exist.
- If no activity found for a competitor, set level to LOW and state what was checked.
- Every HIGH and MEDIUM entry must have a real, working URL.
- Takeaways must be actionable for SMT's sales and partnerships team.
- Focus on Banking and Retail sectors where relevant (SMT's core markets).
- Return ONLY the JSON object, no other text.
"""

print("Calling Claude API...")
message = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=8000,
    messages=[{"role": "user", "content": prompt}]
)

raw_response = message.content[0].text
print("Claude responded. Parsing JSON...")

# Parse JSON — strip any markdown fences if present
clean = raw_response.strip()
if clean.startswith("```"):
    clean = clean.split("```")[1]
    if clean.startswith("json"):
        clean = clean[4:]
clean = clean.strip()

report = json.loads(clean)

# ── MERGE BLOG PROFILES FROM EXISTING DATA ──
# For competitors where Claude didn't update the profile, carry forward existing
comp_profiles = {c['name']: c.get('blog_profile') for c in competitors}
for entry in report["summary"]:
    if not entry.get("blog_profile") and comp_profiles.get(entry["competitor"]):
        entry["blog_profile"] = comp_profiles[entry["competitor"]]
    # Update competitors index with new profile if provided
    if entry.get("blog_profile"):
        for c in competitors:
            if c["name"] == entry["competitor"]:
                c["blog_profile"] = entry["blog_profile"]

# ── SAVE REPORT ──
report_file = f"{today}.json"
report_path = f"{REPORTS_DIR}/{report_file}"
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
print(f"Report saved: {report_path}")

# ── UPDATE MANIFEST ──
manifest["reports"].insert(0, {
    "file": report_file,
    "date": today,
    "label": week_label
})
with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
print("Manifest updated.")

# ── UPDATE COMPETITORS INDEX ──
with open(COMPETITORS_FILE, "w", encoding="utf-8") as f:
    json.dump(competitors, f, indent=2, ensure_ascii=False)
print("Competitors index updated.")

# ── POST TO SLACK ──
if SLACK_BOT_TOKEN:
    print("Posting to Slack...")

    high = [s for s in report["summary"] if s["level"] == "HIGH"]
    medium = [s for s in report["summary"] if s["level"] == "MEDIUM"]
    low = [s for s in report["summary"] if s["level"] == "LOW"]

    def badge(level):
        return {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(level, "⚪")

    sections = "\n\n".join([
        f"{badge(s['level'])} *{s['competitor']} — {s['level']}*\n{s['headline']}"
        + (f"\n→ <{s['url']}|Ver fuente>" if s.get('url') else "")
        for s in report["summary"]
    ])

    takeaways_text = "\n".join([f"{i+1}. {t}" for i, t in enumerate(report["takeaways"])])

    slack_message = f"""📊 *[Igor] Competitive Intel Tracker — {week_label}*
_SMT Tech Partners · {len(report['summary'])} competidores monitoreados · {len(high)} HIGH · {len(medium)} MEDIUM · {len(low)} LOW_

---

*🔍 ANÁLISIS POR COMPETIDOR*

{sections}

---

*💡 CONCLUSIONES CLAVE (VENTAS)*

{takeaways_text}

---
_Generado automáticamente cada viernes 07:30 CET · Portal: https://igortxu79.github.io/cx-intel/_"""

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": SLACK_CHANNEL_ID, "text": slack_message}
    )
    result = resp.json()
    if result.get("ok"):
        print(f"Slack message posted: {result.get('ts')}")
    else:
        print(f"Slack error: {result.get('error')}")
else:
    print("No SLACK_BOT_TOKEN — skipping Slack delivery.")

print(f"\n✅ Tracker complete for {today}")
