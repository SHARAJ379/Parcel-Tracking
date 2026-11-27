# Testing a webhook with webhook.site

1. Open [webhook.site](https://webhook.site/) and copy the unique HTTPS URL it
   gives you.
2. With the app running locally and test keys provisioned, run these
   commands in PowerShell. Replace the webhook URL placeholder with your own
   webhook.site URL. Key values and the one-time webhook secret are kept in
   variables and are not printed.

```powershell
$readKey = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_READ_API_KEY', ''))"
$writeKey = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_WRITE_API_KEY', ''))"
$webhookSiteUrl = 'https://webhook.site/REPLACE-WITH-YOUR-UNIQUE-ID'
$baseUrl = 'http://127.0.0.1:8000'

$registration = Invoke-RestMethod -Uri "$baseUrl/api/v1/webhooks" -Method POST -Headers @{ 'x-api-key' = $writeKey } -ContentType 'application/json' -Body (@{ url = $webhookSiteUrl } | ConvertTo-Json -Compress)
$webhookId = $registration.data.id
$webhookSecret = $registration.data.secret

$trackingId = 'WEBHOOK-TEST-' + [guid]::NewGuid().ToString('N')
$null = Invoke-RestMethod -Uri "$baseUrl/api/v1/parcels" -Method POST -Headers @{ 'x-api-key' = $writeKey } -ContentType 'application/json' -Body (@{ tracking_id = $trackingId; destination = 'Webhook test' } | ConvertTo-Json -Compress)
$null = Invoke-RestMethod -Uri "$baseUrl/api/v1/parcels/$trackingId/status" -Method PATCH -Headers @{ 'x-api-key' = $writeKey } -ContentType 'application/json' -Body (@{ status = 'picked_up'; location = 'Test facility' } | ConvertTo-Json -Compress)
```

3. In webhook.site, open the received request and copy its **raw request body**
   and `X-Signature` header. Verify them in the same PowerShell window:

```powershell
$rawBody = Read-Host 'Paste the exact raw request body'
$receivedSignature = Read-Host 'Paste the X-Signature value'
$hmac = [System.Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($webhookSecret))
$digest = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($rawBody))
$expectedSignature = 'sha256=' + [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
$hmac.Dispose()
if ($expectedSignature -cne $receivedSignature) { throw 'Webhook signature did not match.' }
Write-Output 'Webhook signature verified.'

$null = Invoke-RestMethod -Uri "$baseUrl/api/v1/webhooks/$webhookId" -Method DELETE -Headers @{ 'x-api-key' = $writeKey }
Remove-Variable readKey, writeKey, webhookSecret
```
