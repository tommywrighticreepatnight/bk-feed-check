import requests
import pandas as pd
import re
from datetime import datetime
import os
import sys
import json

YEDOO_EMAIL = os.environ.get("YEDOO_EMAIL")
YEDOO_PASS = os.environ.get("YEDOO_PASS")
FEED_URL = f"http://b2b.yedoo.eu/export/zbozi.php?email={YEDOO_EMAIL}&pass={YEDOO_PASS}"

MY_SKUS_FILE = "yedoo_skus.xlsx"
STATE_FILE = "inventory_previous_yedoo.csv"
SHEET_TAB = "yedoo"

CRITICAL_STOCK = 0
WARNING_STOCK = 3

GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

if not YEDOO_EMAIL or not YEDOO_PASS:
    print("MISSING: YEDOO_EMAIL / YEDOO_PASS env vars")
    sys.exit(1)

print("Fetching Yedoo feed...")
try:
    r = requests.get(FEED_URL, timeout=180)
    r.raise_for_status()
    xml_str = r.content.decode('utf-8', errors='ignore')
    xml_str = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', xml_str)
    print(f"✓ Feed loaded ({len(xml_str) // 1024} KB)")
except Exception as e:
    print(f"FAILED to fetch feed: {e}")
    sys.exit(1)

# Parse: SHOPITEM -> PRODUCT name + multiple VARIANT blocks (EAN + STOCK_AMOUNT)
print("Parsing SHOPITEM/VARIANT elements...")
items = []
for sm in re.finditer(r'<SHOPITEM>(.*?)</SHOPITEM>', xml_str, re.DOTALL):
    block = sm.group(1)
    product_m = re.search(r'<PRODUCT>([^<]+)</PRODUCT>', block)
    product_name = product_m.group(1).strip() if product_m else "Unknown"

    variants = list(re.finditer(r'<VARIANT>(.*?)</VARIANT>', block, re.DOTALL))

    if variants:
        for vm in variants:
            vb = vm.group(1)
            ean = re.search(r'<EAN>([^<]+)</EAN>', vb)
            stock = re.search(r'<STOCK_AMOUNT>([^<]+)</STOCK_AMOUNT>', vb)
            vname = re.search(r'<PRODUCTNAMEEXT>([^<]+)</PRODUCTNAMEEXT>', vb)
            if ean and stock:
                try:
                    items.append({
                        "ean": ean.group(1).strip(),
                        "stock": int(stock.group(1).strip()),
                        "name": product_name + (f" — {vname.group(1).strip()}" if vname else "")
                    })
                except:
                    continue
    else:
        # fallback: no variants, try SHOPITEM level
        ean = re.search(r'<EAN>([^<]+)</EAN>', block)
        stock = re.search(r'<STOCK_AMOUNT>([^<]+)</STOCK_AMOUNT>', block)
        if ean and stock:
            try:
                items.append({
                    "ean": ean.group(1).strip(),
                    "stock": int(stock.group(1).strip()),
                    "name": product_name
                })
            except:
                pass

print(f"Parsed {len(items)} variants from feed")

if not os.path.exists(MY_SKUS_FILE):
    print(f"MISSING: {MY_SKUS_FILE} not found")
    sys.exit(1)

try:
    my_df = pd.read_excel(MY_SKUS_FILE, dtype={"EAN": str, "SKU": str})
    my_df["EAN"] = my_df["EAN"].astype(str).str.strip()
    my_df["SKU"] = my_df["SKU"].astype(str).str.strip()
    ean_to_sku = dict(zip(my_df["EAN"], my_df["SKU"]))
except Exception as e:
    print(f"Error reading {MY_SKUS_FILE}: {e}")
    sys.exit(1)

print(f"Loaded {len(ean_to_sku)} EAN/SKU pairs")

my_eans = set(ean_to_sku.keys())
current = [i for i in items if i["ean"] in my_eans]

if not current:
    print("WARNING: No matching EANs found in feed")
    print(f"First 3 feed EANs: {[i['ean'] for i in items[:3]]}")
    print(f"Your EANs (first 3): {list(my_eans)[:3]}")
    sys.exit(1)

print(f"Tracking {len(current)} of your variants")

# Load previous state (from Google Sheet if available; fallback to CSV)
prev_dict = {}
pending_actions_from_sheet = {}

def open_tab():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    ss = client.open_by_key(GOOGLE_SHEET_ID)
    try:
        return ss.worksheet(SHEET_TAB)
    except Exception:
        return ss.add_worksheet(title=SHEET_TAB, rows=200, cols=12)

if GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID:
    try:
        tab = open_tab()
        all_values = tab.get_all_values()
        if len(all_values) > 1:
            headers = all_values[0]
            ean_idx = headers.index("EAN")
            stock_idx = headers.index("Current Stock")
            action_idx = headers.index("Action Required")
            status_idx = headers.index("Action Status")
            for row in all_values[1:]:
                if len(row) > max(ean_idx, stock_idx, action_idx, status_idx):
                    ean = row[ean_idx].strip()
                    try:
                        prev_dict[ean] = int(row[stock_idx])
                    except:
                        pass
                    if row[action_idx] in ["REMOVE FROM STORE", "ADD TO STORE"] and row[status_idx] == "PENDING":
                        pending_actions_from_sheet[ean] = row[action_idx]
    except Exception as e:
        print(f"Note: could not read previous state from sheet ({e})")

# Fallback to CSV if sheet was empty
if not prev_dict and os.path.exists(STATE_FILE):
    try:
        pdf = pd.read_csv(STATE_FILE, dtype={"ean": str})
        prev_dict = dict(zip(pdf["ean"].astype(str).str.strip(), pdf["stock"].astype(int)))
    except Exception as e:
        print(f"Note: could not read {STATE_FILE} ({e})")

report = []
new_out_of_stock = []
new_restocked = []

for item in current:
    ean = item["ean"]
    sku = ean_to_sku.get(ean, "")
    current_stock = item["stock"]
    prev_stock = prev_dict.get(ean, current_stock)
    change = current_stock - prev_stock
    status = "RESTOCKED" if change > 0 else "SOLD" if change < 0 else "UNCHANGED"

    if current_stock <= CRITICAL_STOCK:
        alert = "OUT OF STOCK"
        if prev_stock > CRITICAL_STOCK and current_stock == CRITICAL_STOCK:
            alert = "NEWLY OUT OF STOCK"
            new_out_of_stock.append(item)
    elif current_stock <= WARNING_STOCK:
        alert = "LOW STOCK"
        if prev_stock == CRITICAL_STOCK and current_stock > CRITICAL_STOCK:
            alert = "RESTOCKED"
            new_restocked.append(item)
    else:
        alert = "OK"
        if prev_stock == CRITICAL_STOCK and current_stock > CRITICAL_STOCK:
            alert = "RESTOCKED"
            new_restocked.append(item)

    action = "NO ACTION"
    action_status = "DONE"
    if ean in pending_actions_from_sheet:
        action = pending_actions_from_sheet[ean]
        action_status = "PENDING"
    else:
        if alert == "NEWLY OUT OF STOCK":
            action = "REMOVE FROM STORE"
            action_status = "PENDING"
        elif alert == "RESTOCKED":
            action = "ADD TO STORE"
            action_status = "PENDING"

    report.append({
        "SKU": sku,
        "EAN": ean,
        "Product": item["name"],
        "Current Stock": current_stock,
        "Previous Stock": prev_stock,
        "Change": change,
        "Status": status,
        "Alert Level": alert,
        "Action Required": action,
        "Action Status": action_status,
        "Last Updated": datetime.now().strftime("%Y-%m-%d %H:%M")
    })

# Backup state
with open(STATE_FILE, "w", encoding="utf-8") as f:
    f.write("ean,stock\n")
    for item in current:
        f.write(f"{item['ean']},{item['stock']}\n")

print(f"\nInventory check complete")
print(f"Total tracked: {len(current)}")
print(f"Newly out of stock: {len(new_out_of_stock)}")
print(f"Restocked: {len(new_restocked)}")
print(f"Low stock (<=3): {len([r for r in report if r['Alert Level'] == 'LOW STOCK'])}")

# Update Google Sheet tab
if GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SHEET_ID:
    try:
        tab = open_tab()
        tab.clear()
        tab.resize(rows=1)
        headers = ["SKU", "EAN", "Product", "Current Stock", "Previous Stock", "Change",
                   "Status", "Alert Level", "Action Required", "Action Status", "Last Updated"]
        rows = [headers] + [[r[h] for h in headers] for r in report]
        tab.update(f"A1", rows, value_input_option="USER_ENTERED")
        print(f"Google Sheets tab '{SHEET_TAB}' updated ({len(report)} rows)")
    except Exception as e:
        print(f"Google Sheets error: {e}")
else:
    print("WARNING: Google Sheets credentials not set")
