import requests
import pandas as pd
import re
from datetime import datetime
import os
import sys
import json

# CONFIG
FEED_URL = "https://b2b.dvedeti.cz/36365?password=36365"
MY_SKUS_FILE = "my_skus.xlsx"  # должен быть в корне репозитория
CRITICAL_STOCK = 0              # отправлять письмо ТОЛЬКО при стоке = 0
WARNING_STOCK = 3               # помечать как "опасные" при <= 3
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
RECIPIENT_EMAILS = [e.strip() for e in os.environ.get("RECIPIENT_EMAILS", "").split(",") if e.strip()]

print("Fetching DveDeti feed...")
try:
    r = requests.get(FEED_URL, timeout=30)
    r.raise_for_status()
    xml = r.content.decode('utf-8')
except Exception as e:
    print(f"FAILED to fetch feed: {e}")
    sys.exit(1)

# Парсим фид
items = []
for match in re.finditer(r'<ZBOZI>(.*?)</ZBOZI>', xml, re.DOTALL):
    item_xml = match.group(1)
    kod = re.search(r'<KOD>(.*?)</KOD>', item_xml)
    stock_txt = re.search(r'<POCETNASKLADE>(.*?)</POCETNASKLADE>', item_xml)
    name = re.search(r'<NAZEV>(.*?)</NAZEV>', item_xml)
    
    if kod and stock_txt:
        try:
            stock = int(stock_txt.group(1))
        except:
            stock = 0
        items.append({
            "sku": kod.group(1).strip().upper(),
            "stock": stock,
            "name": name.group(1).strip() if name else "Unknown"
        })

print(f"Parsed {len(items)} products")

# Загружаем твои SKU
if not os.path.exists(MY_SKUS_FILE):
    print(f"MISSING: {MY_SKUS_FILE} not found")
    sys.exit(1)

my_skus = pd.read_excel(MY_SKUS_FILE)["SKU"].astype(str).str.strip().str.upper().tolist()
current = [i for i in items if i["sku"] in my_skus]

if not current:
    print("WARNING: No matching SKUs found")
    print(f"First 3 feed SKUs: {[i['sku'] for i in items[:3]]}")
    print(f"Your SKUs: {my_skus[:3]}")
    sys.exit(1)

print(f"Tracking {len(current)} of your SKUs")

# Загружаем предыдущее состояние
prev_dict = {}
if os.path.exists("inventory_previous.csv"):
    prev_df = pd.read_csv("inventory_previous.csv")
    prev_dict = dict(zip(prev_df["sku"], prev_df["stock"]))

# Собираем отчёт + ищем НОВЫЕ нулевые стоки
report = []
new_out_of_stock = []
new_warning = []

for item in current:
    prev = prev_dict.get(item["sku"], item["stock"])
    change = item["stock"] - prev
    status = "RESTOCKED" if change > 0 else "SOLD" if change < 0 else "UNCHANGED"
    
    # Определяем уровень алерта
    if item["stock"] <= CRITICAL_STOCK:
        alert = "OUT OF STOCK"
        if prev > CRITICAL_STOCK:  # НОВЫЙ нулевой сток
            new_out_of_stock.append(item)
    elif item["stock"] <= WARNING_STOCK:
        alert = "DANGEROUS"  # <= 3 штук
        if prev > WARNING_STOCK:
            new_warning.append(item)
    else:
        alert = "OK"
    
    report.append({
        "SKU": item["sku"],
        "Product": item["name"],
        "Current Stock": item["stock"],
        "Previous Stock": prev,
        "Change": change,
        "Status": status,
        "Alert Level": alert
    })

# Сохраняем состояние для завтра
pd.DataFrame(current)[["sku", "stock"]].to_csv("inventory_previous.csv", index=False)

# Генерируем Excel
df = pd.DataFrame(report)
df = df.sort_values("Alert Level", key=lambda x: x.map({
    "OUT OF STOCK": 0, "DANGEROUS": 1, "OK": 2, "UNCHANGED": 3
}))
today = datetime.now().strftime("%Y%m%d")
report_file = f"DVEDETI_INVENTORY_{today}.xlsx"
df.to_excel(report_file, index=False)

print(f"DONE. Report: {report_file}")
print(f"Out of stock: {len([r for r in report if r['Alert Level'] == 'OUT OF STOCK'])}")
print(f"Dangerous (<=3): {len([r for r in report if r['Alert Level'] == 'DANGEROUS'])}")

# ОТПРАВЛЯЕМ ПИСЬМО ТОЛЬКО ПРИ НОВЫХ НУЛЕВЫХ СТОКАХ
if new_out_of_stock and SENDGRID_API_KEY and RECIPIENT_EMAILS:
    subject = f"🚨 OUT OF STOCK ALERT - {len(new_out_of_stock)} products"
    body = f"Products that just ran out of stock ({today}):\n\n"
    
    for item in new_out_of_stock:
        body += f"- SKU: {item['sku']} | {item['name']} | Stock: {item['stock']}\n"
    
    body += f"\nFull report attached: {report_file}"
    
    try:
        import requests
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": email} for email in RECIPIENT_EMAILS]}],
                "from": {"email": "alerts@bk-feed-check.com", "name": "BK Inventory Bot"},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}]
            }
        )
        
        if response.status_code == 202:
            print(f"Email sent to {len(RECIPIENT_EMAILS)} recipients")
        else:
            print(f"SendGrid failed: {response.status_code}")
    except Exception as e:
        print(f"Email error: {e}")
elif not new_out_of_stock:
    print("No new out-of-stock items — skipping email")
