# Build all 3 Lambda deployment zips (Windows equivalent of `make package`).
# Run from repo root: .\build.ps1
# Delegates to scripts/package_lambdas.py, which writes Lambda-safe zips
# (forward-slash entry names, Linux/CPython 3.12 wheels).

$ErrorActionPreference = "Stop"
python "$PSScriptRoot\scripts\package_lambdas.py"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "All zips built. Next: cd infra\terraform; terraform plan; terraform apply" -ForegroundColor Yellow
