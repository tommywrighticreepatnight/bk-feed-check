# ОТПРАВКА ЧЕРЕЗ BREVO (бесплатно, без созвонов)
if new_out_of_stock and RECIPIENT_EMAILS:
    brevo_api_key = os.environ.get("BREVO_API_KEY")
    if not brevo_api_key:
        print("WARNING: No BREVO_API_KEY secret set - skipping email")
    else:
        subject = f"🚨 OUT OF STOCK ALERT - {len(new_out_of_stock)} products"
        body = f"Products that just ran out of stock ({today}):\n\n"
        
        for item in new_out_of_stock:
            body += f"- SKU: {item['sku']} | {item['name']} | Stock: {item['stock']}\n"
        
        body += f"\nFull report attached: {report_file}"
        
        try:
            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": brevo_api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "sender": {"email": "alerts@yourdomain.com", "name": "BK Inventory Bot"},
                    "to": [{"email": email} for email in RECIPIENT_EMAILS],
                    "subject": subject,
                    "textContent": body
                }
            )
            
            if response.status_code in [200, 201]:
                print(f"Email sent to {len(RECIPIENT_EMAILS)} recipients via Brevo")
            else:
                print(f"Brevo failed: {response.status_code} {response.text}")
        except Exception as e:
            print(f"Email error: {e}")
