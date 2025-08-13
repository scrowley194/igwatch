# iGaming Earnings Alerts (Starter)

A lightweight Python service that watches **official investor channels** for your core iGaming/casino names, extracts the fields you care about, and sends a **plain‑text email** via Microsoft 365 (Graph) the moment a report drops.

- Monitors: IR RSS/press pages + (optional) SEC EDGAR for US names
- Extracts: Headline, short summary, 5 "controversial" points, EBITDA vs LY, Revenue vs LY, Geo & Product breakdowns, and a brief industry context line
- Sends: Plain‑text email to Outlook/Exchange Online (Graph) or SMTP OAuth
- State: SQLite to avoid duplicate sends

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config/settings.example.env .env
python -m app.main
```

> By default, the loop polls every **60s**. Change `POLL_SECONDS` in `.env`.

### Microsoft 365 Email (Graph recommended)

1. Create an **App registration** in Entra ID (Azure AD).  
2. Add **Application permission**: `Mail.Send`. Grant **admin consent**.  
3. Fill these in `.env`:
   - `GRAPH_TENANT_ID`
   - `GRAPH_CLIENT_ID`
   - `GRAPH_CLIENT_SECRET`
   - `MAIL_FROM` (e.g. newsletter@next.io)
   - `MAIL_TO` (comma‑separated; e.g. stuart@next.io)

Alternatively use **SMTP OAuth** (keeps modern auth; no basic auth).

### Configure companies

Edit `config/companies.yml`. Each company can have one or more **watchers**:

- `edgar_atom`: Uses SEC Atom feeds (by **ticker**; we auto‑map tickers→CIKs at runtime).  
- `rss`: Direct RSS/Atom URL.  
- `rss_page`: A web page that lists RSS links (we auto‑discover `<link rel="alternate" type="application/rss+xml">`).  
- `page`: A press‑release or regulatory‑news listing page (we parse newest article links; conservative & polite).

> Start with RSS or EDGAR where available; use `page` for LSE/Nasdaq Stockholm names that don’t expose RSS.

### Notes

- **User‑Agent**: set a descriptive `SEC_USER_AGENT` in `.env` (SEC asks for this).  
- **Geo/Product tables**: The parser is conservative; it pulls obvious tables and key sentences. We can add **issuer‑specific rules** in `app/parsers/extract.py` as we tune.
- **Dry‑run**: Set `DRY_RUN=true` to log what *would* be sent without emailing.

---

## Structure

```
app/
  main.py              # Orchestrator loop
  config.py            # env & constants
  utils/
    log.py             # logger
    state.py           # sqlite seen-store
    http.py            # polite HTTP (ETag/Last-Modified)
    text.py            # helpers
  watchers/
    base.py            # interface
    rss_watcher.py     # RSS/Atom + RSS page discovery
    page_watcher.py    # generic page parser (Q4/GCS/Cision/WordPress-like)
    edgar_watcher.py   # SEC Atom feed by ticker
  parsers/
    extract.py         # extract metrics & "controversial" items
  emailers/
    graph_mailer.py    # Microsoft Graph sendMail
    smtp_oauth.py      # SMTP with OAuth2 (Exchange Online)
config/
  companies.yml        # the core 15 + watcher definitions
  settings.example.env # template env
```

---

## Security, rate limits & etiquette
- We cache and respect **ETag**/**Last‑Modified** and back off on non‑200s.
- SEC access is modest and declares your **user‑agent**.
- The default 60s poll keeps total volume low and polite.
