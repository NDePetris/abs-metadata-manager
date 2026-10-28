[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$InstallCli,
    [switch]$Check,
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
}
if ($InstallCli) {
    & (Join-Path $PSScriptRoot 'scripts\install-cli.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'abs-cli installation failed.' }
}
& $pythonPath -c 'import streamlit, requests, dotenv, abs_manager'
if ($LASTEXITCODE -ne 0) { throw 'Dependencies missing. Run .\start.ps1 -Install.' }
if ($Check) {
    Write-Output 'Launcher check passed. Python and app dependencies are ready.'
    exit 0
}
& $pythonPath -m streamlit run app.py --server.address 127.0.0.1 --server.port $Port --server.headless true --browser.gatherUsageStats false
exit $LASTEXITCODE
