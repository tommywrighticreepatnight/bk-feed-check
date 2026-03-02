import requests
import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
import os
import sys
import re

# CONFIG
FEED_URL = "https://b2b.dvedeti.cz/36365?password=36365"
MY_SKUS_FILE = "my_skus.xlsx"
CRITICAL_STOCK = 0
WARNING_STOCK = 3
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
RECIPIENT_EMAILS = [e.strip() for e in os.environ.get("RECIPIENT_EMAILS", "").split(",") if e.strip()]

print("Fetching DveDeti feed...")
try:
    r = requests.get(FEED_URL, timeout=120)
    r.raise_for_status()
    xml_content = r.content
    print(f"✓ Feed loaded ({len(xml_content) // 1024 // 1024} MB)")
except Exception as e:
    print(f"FAILED to fetch feed: {e}")
    sys.exit(1)

# Очистка от невалидных символов (часто в чешских фидах)
print("Cleaning XML...")
try:
    xml_str = xml_content.decode('utf-8', errors='ignore')
    xml_str = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', xml_str)
    # Удаляем первую XML декларацию
    xml_str = re.sub(r'<\?xml[^>]+\?>', '', xml_str, count=1)
except Exception as e:
    print(f"XML decode failed: {e}")
    sys.exit(1)

# ПАРСИНГ ЧЕРЕЗ ИТЕРАТОР (память-эффективный для 88 МБ)
print("Parsing SHOPITEM elements...")
items = []
try:
    # Оборачиваем в <root> если нет единого корня
    if not xml_str.strip().startswith('<'):
        raise ET.ParseError("Empty XML")
    
    # Ищем все <SHOPITEM> напрямую через регулярку (быстрее для огромных файлов)
    for match in re.finditer(r'<SHOPITEM>(.*?)</SHOPITEM>', xml_str, re.DOTALL):
        block = match.group(1)
        kod = re.search(r'<KOD>([^<]+)</KOD>', block)
        stock = re.search(r'<POCETNASKLADE>([^<]+)</POCETNASKLADE>', block)
        name = re.search(r'<PRODUCT>([^<]+)</PRODUCT>', block)  # ВНИМАНИЕ: тег называется PRODUCT, не NAZEV
        
        if kod and stock:
            try:
                sku = kod.group(1).strip().upper()
                stock_val = int(stock.group(1).strip())
                product_name = name.group(1).strip() if name else "Unknown"
                items.append({"sku": sku, "stock": stock_val, "name": product_name})
            except:
                continue
except Exception as e:
    print(f"Parsing failed: {e}")
    sys.exit(1)

print(f"Parsed {len(items)} products")

if len(items) == 0:
    print("ERROR: No products parsed. Checking for your SKUs in raw feed...")
    sample_skus = ['MI06', 'MR03S', 'UG70100', 'BJF151']
    for sku in sample_skus:
        if sku in xml_str:
            print(f"  ✓ SKU '{sku}' FOUND in feed")
        else:
            print(f"  ✗ SKU '{sku}' NOT FOUND")
    print("\nFirst 1000 chars of feed:")
    print(xml_str[:1000])
    sys.exit(1)

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
    print("\n💡 FIX: Your SKUs must match <KOD> values EXACTLY (case/spaces)")
    sys.exit(1)

print(f"Tracking {len(current)} of your SKUs")

# Предыдущее состояние
prev_dict = {}
if os.path.exists("inventory_previous.csv"):
    prev_df = pd.read_csv("inventory_previous.csv")
    prev_dict = dict(zip(prev_df["sku"], prev_df["stock"]))

# Отчёт
report = []
new_out_of_stock = []
new_warning = []

for item in current:
    prev = prev_dict.get(item["sku"], item["stock"])
    change = item["stock"] - prev
    status = "RESTOCKED" if change > 0 else "SOLD" if change < 0 else "UNCHANGED"
    
    if item["stock"] <= CRITICAL_STOCK:
        alert = "OUT OF STOCK"
        if prev > CRITICAL_STOCK:
            new_out_of_stock.append(item)
    elif item["stock"] <= WARNING_STOCK:
        alert = "DANGEROUS"
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

# Сохраняем состояние
pd.DataFrame(current)[["sku", "stock"]].to_csv("inventory_previous.csv", index=False)

# Excel отчёт
df = pd.DataFrame(report)
df = df.sort_values("Alert Level", key=lambda x: x.map({
    "OUT OF STOCK": 0, "DANGEROUS": 1, "OK": 2, "UNCHANGED": 3
}))
today = datetime.now().strftime("%Y%m%d")
report_file = f"DVEDETI_INVENTORY_{today}.xlsx"
df.to_excel(report_file, index=False)

print(f"\n✅ DONE")
print(f"Report: {report_file}")
print(f"Out of stock: {len([r for r in report if r['Alert Level'] == 'OUT OF STOCK'])}")
print(f"Dangerous (<=3): {len([r for r in report if r['Alert Level'] == 'DANGEROUS'])}")

# Отправка письма
if new_out_of_stock and BREVO_API_KEY and RECIPIENT_EMAILS:
    subject = f"OUT OF STOCK ALERT - {len(new_out_of_stock)} products"
    body = f"Products that just ran out of stock ({today}):\n\n"
    
    for item in new_out_of_stock:
        body += f"- SKU: {item['sku']} | {item['name']} | Stock: {item['stock']}\n"
    
    body += f"\nFull report attached: {report_file}"
    
    try:
        import requests as req
        response = req.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
            json={
                "sender": {"email": "alerts@bk-feed-check.com", "name": "BK Inventory Bot"},
                "to": [{"email": email} for email in RECIPIENT_EMAILS],
                "subject": subject,
                "textContent": body
            }
        )
        if response.status_code in [200, 201]:
            print(f"Email sent to {len(RECIPIENT_EMAILS)} recipients")
        else:
            print(f"Brevo failed: {response.status_code}")
    except Exception as e:
        print(f"Email error: {e}")
elif not new_out_of_stock:
    print("No new out-of-stock items — skipping email")
