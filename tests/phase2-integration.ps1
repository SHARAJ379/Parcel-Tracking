$ErrorActionPreference = 'Stop'

$readKeyOutput = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_READ_API_KEY', ''))"
$writeKeyOutput = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_WRITE_API_KEY', ''))"
$readKey = ($readKeyOutput | Out-String).Trim()
$writeKey = ($writeKeyOutput | Out-String).Trim()
if (-not $readKey -or -not $writeKey) {
    throw 'Provision local test keys first with: python scripts\provision_phase2_test_keys.py'
}

$baseUrl = 'http://127.0.0.1:8000'

function Get-HttpStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [string]$ApiKey,
        [string]$Body
    )

    $headers = @{}
    if ($ApiKey) {
        $headers['x-api-key'] = $ApiKey
    }

    try {
        $request = @{
            Method = $Method
            Uri = $Uri
            Headers = $headers
        }
        if ($Body) {
            $request['ContentType'] = 'application/json'
            $request['Body'] = $Body
        }
        $response = Invoke-WebRequest @request
        return [int]$response.StatusCode
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            return [int]$_.Exception.Response.StatusCode
        }
        throw
    }
}

function Assert-Status {
    param(
        [string]$Name,
        [int]$Actual,
        [int]$Expected
    )
    if ($Actual -ne $Expected) {
        throw "$Name failed: expected HTTP $Expected, received HTTP $Actual."
    }
    Write-Output "$Name passed (HTTP $Actual)."
}

$knownParcelUrl = "$baseUrl/api/v1/parcels/DEMO-PARCEL-001"
Assert-Status 'Missing API key' (Get-HttpStatus -Method GET -Uri $knownParcelUrl) 401
Assert-Status 'Successful parcel lookup' (Get-HttpStatus -Method GET -Uri $knownParcelUrl -ApiKey $readKey) 200
Assert-Status 'Write scope denied for read-only key' (Get-HttpStatus -Method PATCH -Uri "$knownParcelUrl/status" -ApiKey $readKey -Body '{"status":"in_transit"}') 403
Assert-Status 'Unknown parcel' (Get-HttpStatus -Method GET -Uri "$baseUrl/api/v1/parcels/DOES-NOT-EXIST" -ApiKey $readKey) 404

$trackingId = 'PHASE2-TEST-' + [guid]::NewGuid().ToString('N')
$createBody = @{
    tracking_id = $trackingId
    destination = 'Phase 2 integration test'
} | ConvertTo-Json -Compress
$createdStatus = Get-HttpStatus -Method POST -Uri "$baseUrl/api/v1/parcels" -ApiKey $writeKey -Body $createBody
Assert-Status 'Create parcel' $createdStatus 201

$updateStatus = Get-HttpStatus -Method PATCH -Uri "$baseUrl/api/v1/parcels/$trackingId/status" -ApiKey $writeKey -Body '{"status":"in_transit","location":"Test facility"}'
Assert-Status 'Update parcel status' $updateStatus 200

$rateLimited = $false
for ($attempt = 0; $attempt -lt 65; $attempt++) {
    $status = Get-HttpStatus -Method GET -Uri $knownParcelUrl -ApiKey $readKey
    if ($status -eq 429) {
        $rateLimited = $true
        break
    }
    if ($status -ne 200) {
        throw "Rate-limit test failed early with HTTP $status."
    }
}
if (-not $rateLimited) {
    throw 'Rate-limit test failed: no HTTP 429 received within 65 requests.'
}
Write-Output 'Rate limit passed (HTTP 429).'

Remove-Variable readKeyOutput, writeKeyOutput, readKey, writeKey
