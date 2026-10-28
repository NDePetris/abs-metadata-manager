[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$toolsPath = Join-Path $projectPath '.tools'
$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
if ($architecture -notin @('x64', 'arm64')) { throw 'abs-cli supports Windows x64 and ARM64.' }
$assetName = "abs-cli-win-$architecture.exe"
$releaseUrl = 'https://github.com/thomaslazar/abs-cli/releases/download/v1.2.0'
New-Item -ItemType Directory -Force -Path $toolsPath | Out-Null
$downloadPath = Join-Path $toolsPath "$assetName.download"
$checksumPath = Join-Path $toolsPath 'SHA256SUMS'
Invoke-WebRequest "$releaseUrl/SHA256SUMS" -OutFile $checksumPath
Invoke-WebRequest "$releaseUrl/$assetName" -OutFile $downloadPath
$checksumLine = Get-Content -LiteralPath $checksumPath | Where-Object { $_ -match "\s+\*?$([regex]::Escape($assetName))$" }
if (@($checksumLine).Count -ne 1) { throw 'No unique published checksum found. Binary was not installed.' }
$expectedHash = ($checksumLine -split '\s+')[0]
$actualHash = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash
if ($actualHash -ne $expectedHash) { throw 'Checksum mismatch. Binary was not installed.' }
Move-Item -LiteralPath $downloadPath -Destination (Join-Path $toolsPath 'abs-cli.exe') -Force
Write-Output 'Installed official abs-cli 1.2.0 in .tools after SHA-256 verification.'
