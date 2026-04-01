#!/usr/bin/env python3
"""
QuantAI Google Sheets Journal Sync

Sheet structure:
  - All Trades     : every trade (agent + manual), filterable
  - Agent Trades   : only debate chamber / orchestrator proposals
  - Manual Trades  : only trades Amit executed himself
  - Summary        : live P&L dashboard with formulas

Trade source field:
  - "agent"  : proposed by debate chamber / orchestrator scan
  - "manual" : Amit entered himself in #journal

Usage:
  python3 sheets_sync.py           # sync all trades
  python3 sheets_sync.py --setup   # first-time sheet structure setup
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

SHEET_ID             = os.environ.get("GOOGLE_SHEET_ID", "")
SERVICE_ACCOUNT_FILE = "/home/trader/QuantAI/v2/shared-data/google_service_account.json"
PAPER_JOURNAL        = "/root/quantai-v2/shared-data/journal/paper/trades.jsonl"
REAL_JOURNAL         = "/root/quantai-v2/shared-data/journal/real/trades.jsonl"

if not SHEET_ID:
    print("[sheets_sync] ERROR: GOOGLE_SHEET_ID not in .env"); sys.exit(1)
if not os.path.exists(SERVICE_ACCOUNT_FILE):
    print(f"[sheets_sync] ERROR: {SERVICE_ACCOUNT_FILE} not found"); sys.exit(1)

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
except ImportError:
    os.system("pip3 install google-api-python-client google-auth --break-system-packages -q")
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

creds  = Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
svc    = build("sheets", "v4", credentials=creds).spreadsheets()

# ── Column headers ────────────────────────────────────────────────────
HEADERS = [
    "ID", "Date", "Source", "Mode", "Symbol", "Action",
    "Strike", "Expiry", "Premium", "Contracts", "Total $",
    "Underlying", "Strategy", "Status",
    "Close Date", "Close Premium", "P&L $", "P&L %", "Notes"
]

# ── Helpers ───────────────────────────────────────────────────────────

def get_sheet_id_by_name(name):
    meta = svc.get(spreadsheetId=SHEET_ID).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == name:
            return s["properties"]["sheetId"]
    return None


def ensure_sheets(names):
    meta     = svc.get(spreadsheetId=SHEET_ID).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    requests = []
    for name in names:
        if name not in existing:
            requests.append({"addSheet": {"properties": {"title": name}}})
    if requests:
        svc.batchUpdate(spreadsheetId=SHEET_ID, body={"requests": requests}).execute()
        print(f"[sheets_sync] Created tabs: {[r['addSheet']['properties']['title'] for r in requests]}")


def write_tab(tab_name, rows):
    svc.values().clear(
        spreadsheetId=SHEET_ID,
        range=f"'{tab_name}'!A1:T2000"
    ).execute()
    if rows:
        svc.values().update(
            spreadsheetId=SHEET_ID,
            range=f"'{tab_name}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows}
        ).execute()


def bold_header(tab_name):
    sid = get_sheet_id_by_name(tab_name)
    if sid is None:
        return
    svc.batchUpdate(spreadsheetId=SHEET_ID, body={"requests": [{
        "repeatCell": {
            "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
            }},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"
        }
    }]}).execute()


def color_rows_by_status(tab_name, trades_count):
    """Green for CLOSED wins, red for CLOSED losses, yellow for OPEN."""
    sid = get_sheet_id_by_name(tab_name)
    if sid is None or trades_count == 0:
        return
    # We'll use conditional formatting rules — simpler than per-row coloring
    requests = [
        # OPEN rows → light yellow background
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1, "endRowIndex": trades_count + 1}],
            "booleanRule": {
                "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "OPEN"}]},
                "format": {"backgroundColor": {"red": 1.0, "green": 0.98, "blue": 0.8}}
            }
        }, "index": 0}},
        # CLOSED rows with P&L > 0 → light green
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1, "endRowIndex": trades_count + 1,
                        "startColumnIndex": 16, "endColumnIndex": 17}],  # P&L $ column
            "booleanRule": {
                "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "0"}]},
                "format": {"backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85}}
            }
        }, "index": 1}},
        # CLOSED rows with P&L < 0 → light red
        {"addConditionalFormatRule": {"rule": {
            "ranges": [{"sheetId": sid, "startRowIndex": 1, "endRowIndex": trades_count + 1,
                        "startColumnIndex": 16, "endColumnIndex": 17}],
            "booleanRule": {
                "condition": {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "0"}]},
                "format": {"backgroundColor": {"red": 0.98, "green": 0.85, "blue": 0.85}}
            }
        }, "index": 2}},
    ]
    try:
        svc.batchUpdate(spreadsheetId=SHEET_ID, body={"requests": requests}).execute()
    except Exception:
        pass  # conditional format may already exist


def trade_to_row(t):
    try:
        date_str = datetime.fromisoformat(t.get("timestamp","")).strftime("%Y-%m-%d %H:%M")
    except:
        date_str = t.get("timestamp","")[:16]

    try:
        close_date = datetime.fromisoformat(t.get("timestamp_close","")).strftime("%Y-%m-%d %H:%M")
    except:
        close_date = ""

    premium   = float(t.get("premium", 0) or 0)
    contracts = int(t.get("contracts", 1) or 1)
    total     = round(premium * contracts * 100, 2)

    return [
        t.get("id", ""),
        date_str,
        t.get("source", "manual"),       # "agent" or "manual"
        t.get("mode", "paper"),
        t.get("symbol", ""),
        t.get("action", ""),
        t.get("strike", ""),
        t.get("expiry", ""),
        premium,
        contracts,
        total,
        t.get("underlying_price", ""),
        t.get("strategy", ""),
        t.get("status", "OPEN"),
        close_date,
        t.get("close_premium", ""),
        t.get("pnl", ""),
        t.get("pnl_pct", ""),
        t.get("notes", ""),
    ]


def load_trades():
    trades = []
    for path in [PAPER_JOURNAL, REAL_JOURNAL]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.strip():
                        try:
                            trades.append(json.loads(line))
                        except:
                            continue
    trades.sort(key=lambda t: t.get("timestamp", ""))
    return trades


# ── Setup ─────────────────────────────────────────────────────────────

def setup():
    ensure_sheets(["All Trades", "Agent Trades", "Manual Trades", "Summary"])

    # Write headers to trade tabs
    for tab in ["All Trades", "Agent Trades", "Manual Trades"]:
        write_tab(tab, [HEADERS])
        bold_header(tab)

    # Summary tab
    summary_rows = [
        ["QuantAI Trade Journal", "", ""],
        ["Last updated", "", ""],
        ["Sheet", "https://docs.google.com/spreadsheets/d/" + SHEET_ID, ""],
        ["", "", ""],
        ["━━━ ALL TRADES ━━━", "", ""],
        ["Total trades",     "=COUNTA('All Trades'!A2:A2000)", ""],
        ["Open positions",   "=COUNTIF('All Trades'!N2:N2000,\"OPEN\")", ""],
        ["Closed trades",    "=COUNTIF('All Trades'!N2:N2000,\"CLOSED\")", ""],
        ["Win rate",         "=IFERROR(COUNTIF('All Trades'!Q2:Q2000,\">0\")/COUNTIF('All Trades'!N2:N2000,\"CLOSED\"),\"N/A\")", ""],
        ["Total P&L $",      "=SUM('All Trades'!Q2:Q2000)", ""],
        ["Total premium $",  "=SUMIF('All Trades'!F2:F2000,\"SELL*\",'All Trades'!K2:K2000)", ""],
        ["", "", ""],
        ["━━━ AGENT TRADES ━━━", "", ""],
        ["Agent trades",     "=COUNTA('Agent Trades'!A2:A2000)", ""],
        ["Agent win rate",   "=IFERROR(COUNTIF('Agent Trades'!Q2:Q2000,\">0\")/COUNTIF('Agent Trades'!N2:N2000,\"CLOSED\"),\"N/A\")", ""],
        ["Agent P&L $",      "=SUM('Agent Trades'!Q2:Q2000)", ""],
        ["", "", ""],
        ["━━━ MANUAL TRADES ━━━", "", ""],
        ["Manual trades",    "=COUNTA('Manual Trades'!A2:A2000)", ""],
        ["Manual win rate",  "=IFERROR(COUNTIF('Manual Trades'!Q2:Q2000,\">0\")/COUNTIF('Manual Trades'!N2:N2000,\"CLOSED\"),\"N/A\")", ""],
        ["Manual P&L $",     "=SUM('Manual Trades'!Q2:Q2000)", ""],
        ["", "", ""],
        ["━━━ OPEN POSITIONS ━━━", "", ""],
        ["Symbol", "Strike / Expiry", "Premium"],
    ]
    write_tab("Summary", summary_rows)

    # Bold summary headers
    sid = get_sheet_id_by_name("Summary")
    if sid:
        bold_rows = [0, 4, 12, 16, 22]  # rows to bold
        requests = []
        for row in bold_rows:
            requests.append({"repeatCell": {
                "range": {"sheetId": sid, "startRowIndex": row, "endRowIndex": row + 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold"
            }})
        svc.batchUpdate(spreadsheetId=SHEET_ID, body={"requests": requests}).execute()

    print("[sheets_sync] ✅ Sheet structure created")
    print(f"[sheets_sync] Open: https://docs.google.com/spreadsheets/d/{SHEET_ID}")


# ── Sync ──────────────────────────────────────────────────────────────

def sync():
    trades = load_trades()
    if not trades:
        print("[sheets_sync] No trades to sync")
        return

    agent_trades  = [t for t in trades if t.get("source") == "agent"]
    manual_trades = [t for t in trades if t.get("source") != "agent"]  # default = manual

    all_rows    = [HEADERS] + [trade_to_row(t) for t in trades]
    agent_rows  = [HEADERS] + [trade_to_row(t) for t in agent_trades]
    manual_rows = [HEADERS] + [trade_to_row(t) for t in manual_trades]

    write_tab("All Trades",    all_rows)
    write_tab("Agent Trades",  agent_rows)
    write_tab("Manual Trades", manual_rows)

    # Color coding
    color_rows_by_status("All Trades",    len(trades))
    color_rows_by_status("Agent Trades",  len(agent_trades))
    color_rows_by_status("Manual Trades", len(manual_trades))

    # Update timestamp in Summary
    svc.values().update(
        spreadsheetId=SHEET_ID,
        range="Summary!B2",
        valueInputOption="USER_ENTERED",
        body={"values": [[datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")]]}
    ).execute()

    print(f"[sheets_sync] ✅ Synced {len(trades)} trades total")
    print(f"[sheets_sync]    Agent: {len(agent_trades)} | Manual: {len(manual_trades)}")
    print(f"[sheets_sync]    Open: https://docs.google.com/spreadsheets/d/{SHEET_ID}")


# ── Main ──────────────────────────────────────────────────────────────
if "--setup" in sys.argv:
    setup()
else:
    sync()
