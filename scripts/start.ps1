param([ValidateSet('api', 'worker')][string]$Role = 'api')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (Test-Path -LiteralPath '.env') {
    foreach ($line in Get-Content -LiteralPath '.env') {
        if ($line -match '^([A-Z_]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), 'Process')
        }
    }
}
if ($Role -eq 'api') {
    & '.\.venv\Scripts\python.exe' -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
} else {
    & '.\.venv\Scripts\python.exe' -m backend.worker
}

