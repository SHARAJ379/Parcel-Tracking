$ErrorActionPreference = 'Stop'

$readKeyOutput = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_READ_API_KEY', ''))"
$writeKeyOutput = & .\.venv\Scripts\python.exe -c "from dotenv import dotenv_values; print(dotenv_values('.env.local').get('PARCEL_TEST_WRITE_API_KEY', ''))"
$readKey = ($readKeyOutput | Out-String).Trim()
$writeKey = ($writeKeyOutput | Out-String).Trim()
if (-not $readKey -or -not $writeKey) {
    throw 'Provision local test keys first with: python scripts\provision_phase2_test_keys.py'
}

$testRateLimit = 0
$hasTestRateLimit = [int]::TryParse($env:RATE_LIMIT_REQUESTS, [ref]$testRateLimit)
if (-not $hasTestRateLimit -or $testRateLimit -lt 1) {
    throw 'Set RATE_LIMIT_REQUESTS to the same low test value used by the server, for example 2.'
}
$maxRateLimitAttempts = $testRateLimit + 1
if ($env:PARCEL_API_BASE_URL) {
    $baseUrl = $env:PARCEL_API_BASE_URL.TrimEnd('/')
}
else {
    $baseUrl = 'http://127.0.0.1:8000'
}

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
        $response = Invoke-WebRequest @request -UseBasicParsing
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

try {
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
    for ($attempt = 1; $attempt -le $maxRateLimitAttempts; $attempt++) {
        $progressStatus = "Authenticated request $attempt of $maxRateLimitAttempts"
        $progressPercent = ($attempt / $maxRateLimitAttempts) * 100
        Write-Progress -Activity 'Testing API rate limit' -Status $progressStatus -PercentComplete $progressPercent
        $status = Get-HttpStatus -Method GET -Uri $knownParcelUrl -ApiKey $readKey
        Write-Output "Rate-limit probe $attempt/$maxRateLimitAttempts -> HTTP $status"
        if ($status -eq 429) {
            $rateLimited = $true
            break
        }
        if ($status -ne 200) {
            throw "Rate-limit test failed early with HTTP $status."
        }
    }
    Write-Progress -Activity 'Testing API rate limit' -Completed
    if (-not $rateLimited) {
        throw "Rate-limit test failed: no HTTP 429 received within $maxRateLimitAttempts requests."
    }
    Write-Output 'Rate limit passed (HTTP 429).'
}
finally {
    Write-Progress -Activity 'Testing API rate limit' -Completed
    Remove-Variable readKeyOutput, writeKeyOutput, readKey, writeKey -ErrorAction SilentlyContinue
}
