# Insta Warm – Realtor Lead Bot

Scrolls your Instagram feed in a real Chrome window, finds sponsored posts aimed at
realtors, opens their likers, and saves the realtor profiles to `data/realtor_leads.csv`.

## One-time setup

```bash
cd "Insta Warm realtor automation"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # only needed if Google Chrome isn't installed
```

**Gemini (Vertex AI, billed to your GCP credits)**
1. In the GCP console, pick the project linked to your credits billing account and enable the **Vertex AI API**.
2. Auth, either:
   - **A:** install the gcloud CLI, run `gcloud auth application-default login`, and set `GOOGLE_CLOUD_PROJECT=<project-id>` in `.env`, or
   - **B:** create a Vertex AI API key (Vertex AI → API keys) and set `VERTEX_API_KEY=` in `.env`.

## Run

```bash
source .venv/bin/activate
python -m realtor_bot.main
```

First run: a Chrome window opens. Log in to Instagram there, then press Enter in the terminal.
The login is saved in `browser_profile/`, so later runs start straight away.

Stop anytime with `Ctrl+C`; the CSV is written on every new lead and again at exit.

## Limits (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `MAX_PROFILE_VISITS_PER_DAY` | 50 | Hard daily cap on profile visits |
| `MAX_VISITS_PER_AD` | 15 | Profiles opened per realtor ad |
| `MAX_LIKERS_PER_AD` | 60 | How far down the likers list to read |
| `SESSION_MINUTES` | 25 | Session length |

Start low on a new/warming account and raise slowly.

## Behaviour

- Stops immediately on any challenge, "action blocked", or logout — resolve it by hand and rest the account.
- Gemini is only called when keywords can't decide; known realtor advertisers are remembered.
- State lives in `data/realtors.db`, so profiles and ads are never processed twice across runs.

## Output columns

username, profile_url, full_name, category, followers, bio, link_in_bio, brokerage, city,
email, phone, source_advertiser, found_at
