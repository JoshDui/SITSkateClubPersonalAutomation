# Build all 3 Lambda deployment zips.
# Windows equivalent of `make package`. Run from repo root: .\build.ps1

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$distDir = "$repoRoot\dist"
New-Item -ItemType Directory -Force -Path $distDir | Out-Null

foreach ($svc in @("webhook", "scheduler", "exporter")) {
    Write-Host ""
    Write-Host "== Building $svc ==" -ForegroundColor Cyan
    $svcDir = "$repoRoot\services\$svc"
    $buildDir = "$svcDir\build"

    if (Test-Path $buildDir) {
        Remove-Item -Recurse -Force $buildDir
    }
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

    # Install pure-Python deps into the build dir (so they're bundled in the zip)
    python -m pip install -r "$svcDir\requirements.txt" -t $buildDir --quiet

    # Copy shared module + the service's handler
    Copy-Item -Recurse "$repoRoot\shared" "$buildDir\shared"
    Copy-Item "$svcDir\handler.py" $buildDir

    # Zip everything inside build/ (but not the build/ dir itself)
    $zipPath = "$distDir\$svc.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    Compress-Archive -Path "$buildDir\*" -DestinationPath $zipPath

    $sizeMB = (Get-Item $zipPath).Length / 1MB
    Write-Host ("  Built dist/{0}.zip ({1:N2} MB)" -f $svc, $sizeMB) -ForegroundColor Green
}

Write-Host ""
Write-Host "All zips built. Next: cd infra\terraform; terraform init; terraform apply" -ForegroundColor Yellow
