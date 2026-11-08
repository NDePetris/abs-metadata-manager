[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$InstallCli,
    [switch]$Check,
    [switch]$Legacy,
    [int]$Port = 8501
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $basePython = Get-Command py -ErrorAction SilentlyContinue
    if ($basePython) { & py -3 -m venv .venv }
    else { & python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 or newer, then run start.ps1 again.' }
    $Install = $true
}
& $pythonPath -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12+ required'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
if ($Install) {
    & $pythonPath -m pip install -r requirements.lock.txt -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if (-not $Legacy) {
        Push-Location (Join-Path $PSScriptRoot 'frontend')
        try {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed. Install Node.js 20.19+.' }
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
        } finally { Pop-Location }
    }
}
if ($InstallCli) {
    & (Join-Path $PSScriptRoot 'scripts\install-cli.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'abs-cli installation failed.' }
}
& $pythonPath -c 'import streamlit, requests, fastapi, uvicorn, abs_manager'
if ($LASTEXITCODE -ne 0) { throw 'Dependencies missing. Run .\start.ps1 -Install.' }
if (-not $Legacy -and -not (Test-Path -LiteralPath 'frontend\dist\index.html')) {
    throw 'Frontend bundle missing. Run .\start.ps1 -Install (requires Node.js only for building).'
}
if ($Check) {
    Write-Output 'Launcher check passed. Python and app dependencies are ready.'
    exit 0
}
if ($Legacy) {
    & $pythonPath -c 'import os; from pathlib import Path; from abs_manager.bulk import Bulk; p=Path(os.getenv("ABS_MM_DB","data/metadata.sqlite3")); b=Bulk(None,p.parent/"bulk",p); assert not any(r["state"] in ("intent","uncertain") for r in b.records().values()), "Unresolved bulk write: check results in the bulk app first"'
    if ($LASTEXITCODE -ne 0) { throw 'Bulk recovery is required before fallback launch.' }
    & $pythonPath -m streamlit run app.py --server.address 127.0.0.1 --server.port $Port --server.headless true --browser.gatherUsageStats false
} else {
    Write-Output "Open http://127.0.0.1:$Port"
    & $pythonPath -m uvicorn abs_manager.bulk_app:app --host 127.0.0.1 --port $Port --no-access-log
}
exit $LASTEXITCODE
