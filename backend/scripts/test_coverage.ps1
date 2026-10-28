$ErrorActionPreference = 'Stop'
$backendRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $backendRoot '.venv\Scripts\python.exe'
$coverageData = Join-Path $env:TEMP 'erp-backend-coverage.db'

Push-Location $backendRoot
try {
    & $pythonPath -m coverage run --branch --source=app --data-file=$coverageData -m pytest tests -q -p no:anyio -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonPath -m coverage report --data-file=$coverageData --fail-under=75
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
