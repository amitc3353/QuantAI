#!/usr/bin/env python3
"""
QuantAI Google Sheets Journal Sync
Syncs trades.jsonl to a Google Sheet so Amit can view trades from his phone.

Setup (one-time):
1. Go to console.cloud.google.com
2. Create project → Enable Google Sheets API + Google Drive API
3. Create Service Account → download JSON key
4. Save key as: /home/trader/QuantAI/v2/shared-data/google_service_account.json
5. Share your Google Sheet with the service account email (Editor access)
6. Add GOOGLE_SHEET_ID to /home/trader/QuantAI/.env

Usage:
  python3 sheets_sync.py              # sync all trades
  python3 sheets_sync.py --setup      # create sheet structure on first run
"""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Auto-load .env
import pathlib as _pl
for _ef in [_pl.Path("/home/trader/QuantAI/.env"), _pl.Path("/root/quantai-v2/.env")]:
    if _ef.exists():
        for _line in _ef.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                if not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()
        break

ET = ZoneInfo("America/New_York")

SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
SERVICE_ACCOUNT_FILE = "/home/trader/QuantAI/v2/shared-data/google_service_account.json"
JOURNAL_PATH = "/home/trader/QuantAI/v2/shared-data/journal/paper/trades.jsonl"
REAL_JOURNAL_PATH = "/home/trader/QuantAI/v2/shared-data/journal/real/trades.jsonl"

# ── Preflight checks ──────────────────────────────────────────────────
if not SHEET_ID:
    print("[sheets_sync] ERROR: GOOGLE_SHEET_ID not set in .env")
    print("  Add: GOOGLE_SHEET_ID=your_sheet_id_from_url")
    sys.exit(1)

if not os.path.exists(SERVICE_ACCOUNT_FILE):
    print(f"[sheets_sync] ERROR: Service account file not found at {SERVICE_ACCOUNT_FILE}")
    print("  Setup instructions:")
    print("  1. console.cloud.google.com → New Project")
    print("  2. Enable: Google Sheets API, Google Drive API")
    print("  3. IAM → Service Accounts → Create → Download JSON key")
    print(f"  4. Save key to: {SERVICE_ACCOUNT_FILE}")
    print("  5. Share your Google Sheet with the service account email (Editor)")
    sys.exit(1)

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
except ImportError:
    print("[sheets_sync] Installing google-api-python-client...")
    os.system("pip3 install google-api-python-client google-auth --break-system-packages -q")
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build("sheets", "v4", credentials=creds)
sheets = service.spreadsheets()

# ── Sheet structure ───────────────────────────────────────────────────
HEADERS = [
    "ID", "Date", "Mode", "Symbol", "Action", "Strike", "Expiry",
    "Premium", "Contracts", "Total Premium $", "Underlying Price",
    "Strategy", "Status", "Close Date", "Close Premium", "P&L $", "P&L %", "Notes"
]

SUMMARY_HEADERS = [
    ["QuantAI Trade Journal", ""],
    ["Last updated", ""],
    ["", ""],
    ["SUMMARY", ""],
    ["Total trades", "=COUNTA(Trades!A2:A1000)-1"],
    ["Open positions", "=COUNTIF(Trades!M2:M1000,\"OPEN\")"],
    ["Closed trades", "=COUNTIF(Trades!M2:M1000,\"CLOSED\")"],
    ["Win rate", "=IFERROR(COUNTIF(Trades!P2:P1000,\">0\")/COUNTIF(Trades!M2:M1000,\"CLOSED\"),0)"],
    ["Total P&L $", "=SUM(Trades!P2:P1000)"],
    ["Total premium collected", "=SUMIF(Trades!D2:D1000,\"*SELL*\",Trades!J2:J1000)"],
    ["", ""],
    ["OPEN POSITIONS", ""],
]


def setup_sheet():
    """Create Trades and Summary tabs with headers."""
    # Get existing sheets
    meta = sheets.get(spreadsheetId=SHEET_ID).execute()
    existing = [s["properties"]["title"] for s in meta.get("sheets", [])]

    requests = []

    if "Trades" not in existing:
        requests.append({"addSheet": {"properties": {"title": "Trades"}}})
    if "Summary" not in existing:
        requests.append({"addSheet": {"properties": {"title": "Summary"}}})

    if requests:
        sheets.batchUpdate(spreadsheetId=SHEET_ID, body={"requests": requests}).execute()
        print("[sheets_sync] Created Trades and Summary tabs")

    # Write headers to Trades tab
    sheets.values().update(
        spreadsheetId=SHEET_ID,
        range="Trades!A1:R1",
        valueInputOption="USER_ENTERED",
        body={"values": [HEADERS]}
    ).execute()

    # Write Summary tab
    sheets.values().update(
        spreadsheetId=SHEET_ID,
        range="Summary!A1:B20",
        valueInputOption="USER_ENTERED",
        body={"values": SUMMARY_HEADERS}
    ).execute()

    # Format header row bold
    requests = [{
        "repeatCell": {
            "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold"
        }
    }]
    sheets.batchUpdate(spreadsheetId=SHEET_ID, body={"requests": requests}).execute()
    print("[sheets_sync] Sheet structure ready")


def load_trades():
    """Load all trades from paper and real journals."""
    trades = []
    for path, mode in [(JOURNAL_PATH, "paper"), (REAL_JOURNAL_PATH, "real")]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.strip():
                        try:
                            t = json.loads(line)
                            t["_mode"] = mode
                            trades.append(t)
                        except:
                            continue
    trades.sort(key=lambda t: t.get("timestamp", ""))
    return trades


def trade_to_row(t):
    """Convert trade dict to sheet row."""
    # Parse date
    try:
        ts = datetime.fromisoformat(t.get("timestamp", ""))
        date_str = ts.strftime("%Y-%m-%d %H:%M")
    except:
        date_str = t.get("timestamp", "")[:16]

    try:
        close_ts = datetime.fromisoformat(t.get("timestamp_close", ""))
        close_date = close_ts.strftime("%Y-%m-%d %H:%M")
    except:
        close_date = ""

    premium = t.get("premium", 0) or 0
    contracts = t.get("contracts", 1) or 1
    total_premium = round(premium * contracts * 100, 2)
    pnl = t.get("pnl", "")
    pnl_pct = t.get("pnl_pct", "")

    return [
        t.get("id", ""),
        date_str,
        t.get("mode", t.get("_mode", "paper")),
        t.get("symbol", ""),
        t.get("action", ""),
        t.get("strike", ""),
        t.get("expiry", ""),
        premium,
        contracts,
        total_premium,
        t.get("underlying_price", ""),
        t.get("strategy", ""),
        t.get("status", "OPEN"),
        close_date,
        t.get("close_premium", ""),
        pnl,
        pnl_pct,
        t.get("notes", ""),
    ]


def sync():
    """Full sync: clear trades tab and rewrite from journal."""
    trades = load_trades()
    if not trades:
        print("[sheets_sync] No trades to sync")
        return

    rows = [HEADERS] + [trade_to_row(t) for t in trades]

    # Clear existing data
    sheets.values().clear(
        spreadsheetId=SHEET_ID,
        range="Trades!A1:R1000"
    ).execute()

    # Write all rows
    sheets.values().update(
        spreadsheetId=SHEET_ID,
        range=f"Trades!A1:R{len(rows)}",
        valueInputOption="USER_ENTERED",
        body={"values": rows}
    ).execute()

    # Update last-updated timestamp in Summary
    sheets.values().update(
        spreadsheetId=SHEET_ID,
        range="Summary!B2",
        valueInputOption="USER_ENTERED",
        body={"values": [[datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")]]}
    ).execute()

    open_count = len([t for t in trades if t.get("status") == "OPEN"])
    closed_count = len([t for t in trades if t.get("status") == "CLOSED"])
    print(f"[sheets_sync] ✅ Synced {len(trades)} trades ({open_count} open, {closed_count} closed)")
    print(f"[sheets_sync] Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")


# ── Main ──────────────────────────────────────────────────────────────
if "--setup" in sys.argv:
    setup_sheet()
    print("[sheets_sync] Setup complete. Now sync with: python3 sheets_sync.py")
else:
    if "--setup-first" in sys.argv or not SHEET_ID:
        setup_sheet()
    sync()
