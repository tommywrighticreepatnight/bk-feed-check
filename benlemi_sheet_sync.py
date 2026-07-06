#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
benlemi_sheet_sync.py — write the Benlemi delivery-window check into a Google
Sheet tab (default) with a persistent pending/done status, OR into a local CSV
(--backend local) for offline testing.

The sheet is a living dashboard: each run refreshes Feed/Current/Target/Action,
resets rows to `pending` when a NEW change is needed, and keeps rows you marked
`done` quiet until the feed changes again. You edit only the `Status` column.

Real run (in GitHub Actions), writes tab `benlemi` in the shared sheet:
  GOOGLE_SERVICE_ACCOUNT_JSON=<...> python3 benlemi_sheet_sync.py \
      --feed "$BENLEMI_FEED_URL" --export data/products_export_DE.csv \
      --backend gsheet --gsheet-id 1TBHy5JnGQ1iCOXw_gV_-cl_slRJ-vWlpZUGPVpwlJ9Q --tab benlemi

Offline test, uses a local CSV as the "sheet" (read prior -> write new):
  python3 benlemi_sheet_sync.py --feed data/synthetic_feed_demo.xml \
      --export products.csv --backend local --sheet-csv out/benlemi_tab.csv
"""
import argparse, csv, json, os
from benlemi_pipeline import load_feed, load_export, decide
from benlemi_state import build_rows, HEADER


# ---- backends: read prior rows, write new rows -----------------------------
def local_read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def local_write(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


def gsheet_open(sheet_id, tab):
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])  # same secret as yedoo checker
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    gc = gspread.authorize(Credentials.from_service_account_info(info, scopes=scopes))
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=200, cols=len(HEADER))
    return ws


def gsheet_read(ws):
    values = ws.get_all_values()
    if not values:
        return []
    head, *body = values
    return [dict(zip(head, r)) for r in body]


def gsheet_write(ws, rows):
    ws.clear()
    data = [HEADER] + [[r.get(c, "") for c in HEADER] for r in rows]
    ws.update(data, value_input_option="RAW")


# ---- main ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", required=True)
    ap.add_argument("--export", required=True)
    ap.add_argument("--backend", choices=["gsheet", "local"], default="gsheet")
    ap.add_argument("--gsheet-id")
    ap.add_argument("--tab", default="benlemi")
    ap.add_argument("--sheet-csv", help="local backend: path acting as the sheet")
    a = ap.parse_args()

    feed = load_feed(a.feed)
    products = load_export(a.export)
    decisions = [(p, decide(p, feed)) for p in products.values()]

    if a.backend == "local":
        assert a.sheet_csv, "--sheet-csv required for --backend local"
        prior = local_read(a.sheet_csv)
    else:
        assert a.gsheet_id, "--gsheet-id required for --backend gsheet"
        ws = gsheet_open(a.gsheet_id, a.tab)
        prior = gsheet_read(ws)
    prior_by_handle = {r.get("Handle", ""): r for r in prior}

    rows, counts = build_rows(decisions, prior_by_handle)

    if a.backend == "local":
        local_write(a.sheet_csv, rows)
        dest = a.sheet_csv
    else:
        gsheet_write(ws, rows)
        dest = f"gsheet:{a.gsheet_id}#{a.tab}"

    pending = sum(1 for r in rows if r["Status"] == "pending")
    review = sum(1 for r in rows if r["Status"] == "review")
    done = sum(1 for r in rows if r["Status"] == "done")
    print("feed items:", len(feed), "| products:", len(products), "| rows:", len(rows))
    print("counts:", dict(sorted(counts.items(), key=lambda x: -x[1])))
    print(f"pending={pending} review={review} done={done} -> {dest}")


if __name__ == "__main__":
    main()
