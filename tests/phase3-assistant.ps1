$ErrorActionPreference = 'Stop'

$readKeyOutput = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_READ_API_KEY', ''))"
$readKey = ($readKeyOutput | Out-String).Trim()
if (-not $readKey) {
    throw 'Provision a read key first with: python scripts\provision_phase2_test_keys.py'
}

if ($env:PARCEL_API_BASE_URL) {
    $baseUrl = $env:PARCEL_API_BASE_URL.TrimEnd('/')
}
else {
    $baseUrl = 'http://127.0.0.1:8000'
}

function Assert-HttpStatus {
    param(
        [string]$Name,
        [scriptblock]$Request,
        [int]$Expected
    )

    try {
        $response = & $Request
        $actual = [int]$response.StatusCode
    }
    catch {
        if (-not $_.Exception.Response -or -not $_.Exception.Response.StatusCode) {
            throw
        }
        $actual = [int]$_.Exception.Response.StatusCode
    }

    if ($actual -ne $Expected) {
        throw "$Name failed: expected HTTP $Expected, received HTTP $actual."
    }
    Write-Output "$Name passed (HTTP $actual)."
}

try {
    $assistantUrl = "$baseUrl/api/v1/assistant"
    $questionBody = @{
        tracking_id = 'DEMO-PARCEL-001'
        question = 'What is the current parcel status?'
    } | ConvertTo-Json -Compress

    Assert-HttpStatus 'Missing API key' {
        Invoke-WebRequest -UseBasicParsing -Uri $assistantUrl -Method POST -ContentType 'application/json' -Body $questionBody
    } 401

    $notFoundBody = @{
        tracking_id = 'DOES-NOT-EXIST'
        question = 'What is the parcel status?'
    } | ConvertTo-Json -Compress
    Assert-HttpStatus 'Unknown parcel' {
        Invoke-WebRequest -UseBasicParsing -Uri $assistantUrl -Method POST -Headers @{ 'x-api-key' = $readKey } -ContentType 'application/json' -Body $notFoundBody
    } 404

    $invalidBody = @{
        tracking_id = 'DEMO-PARCEL-001'
        question = 'q' * 1001
    } | ConvertTo-Json -Compress
    Assert-HttpStatus 'Question length validation' {
        Invoke-WebRequest -UseBasicParsing -Uri $assistantUrl -Method POST -Headers @{ 'x-api-key' = $readKey } -ContentType 'application/json' -Body $invalidBody
    } 400

    $response = Invoke-RestMethod -Uri $assistantUrl -Method POST -Headers @{ 'x-api-key' = $readKey } -ContentType 'application/json' -Body $questionBody -TimeoutSec 30
    if (-not $response.success -or -not $response.data.answer) {
        throw 'Live Gemini assistant response did not contain an answer.'
    }
    Write-Output 'Live assistant request passed (HTTP 200; answer received).'
}
finally {
    Remove-Variable readKeyOutput, readKey -ErrorAction SilentlyContinue
}
