<#
.SYNOPSIS
    Builds the Windows program: dist\ThrashDash-<version>-windows-x64.zip

.DESCRIPTION
    Needs Python 3.10 to 3.13 (64-bit, from python.org, with the "py launcher" option) and nothing else: every package it needs
    has a ready-made Windows build, so no compiler is required. Everything it makes goes under build\ and dist\.

    Run it from anywhere:   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
    or double-click:        packaging\build-windows.bat

    UNTESTED on Windows so far (it was written on Linux, where PowerShell is not available to run it). If a step fails, the
    message says which; please report it.
#>
$ErrorActionPreference = "Continue"   # native programs are checked by exit code below (more reliable than PowerShell's own handling of their output)

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Build = Join-Path $Root "build"
$Dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $Build, $Dist | Out-Null
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Build "pyinstaller-cache"   # keep its cache here rather than in the user profile

function Stop-Build([string]$Message) {
    Write-Host ""
    Write-Host "BUILD FAILED: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Native([string]$Description, [string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Stop-Build "$Description (exit code $LASTEXITCODE)" }
}

Write-Host "== Finding Python"
$PyVersion = $null
foreach ($candidate in @("3.13", "3.12", "3.11", "3.10")) {
    & py "-$candidate" -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $PyVersion = $candidate; break }
}
if (-not $PyVersion) {
    Stop-Build "No 64-bit Python 3.10 to 3.13 was found. Install one from https://www.python.org/downloads/ and keep the 'py launcher' option ticked."
}
Write-Host "using Python $PyVersion"

Write-Host "== Python environment"
$Venv = Join-Path $Build "venv-win-$PyVersion"   # one per Python version
$VenvPython = Join-Path $Venv "Scripts\python.exe"
Invoke-Native "creating the environment" "py" @("-$PyVersion", "-m", "venv", $Venv)
Invoke-Native "upgrading pip" $VenvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Native "installing the requirements" $VenvPython @("-m", "pip", "install", "--quiet", "-r", (Join-Path $Root "requirements.txt"), "-r", (Join-Path $Root "packaging\requirements-build.txt"))

$Version = (& $VenvPython -c "import sys; sys.path.insert(0, sys.argv[1]); import thrashdash; print(thrashdash.__version__)" (Join-Path $Root "relay")).Trim()
if (-not $Version) { Stop-Build "could not read the version number" }

Write-Host "== Freezing ThrashDash $Version with PyInstaller"
$PyiDist = Join-Path $Build "pyi-dist-win"
$PyiWork = Join-Path $Build "pyi-work-win"
Invoke-Native "PyInstaller" $VenvPython @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", $PyiDist, "--workpath", $PyiWork, (Join-Path $Root "packaging\thrashdash.spec"))
$Frozen = Join-Path $PyiDist "thrashdash"
if (-not (Test-Path (Join-Path $Frozen "thrashdash.exe"))) { Stop-Build "PyInstaller did not produce thrashdash.exe" }

Write-Host "== Assembling the folder"
$Name = "ThrashDash-$Version-windows-x64"
$Out = Join-Path $Dist $Name
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
Copy-Item -Recurse $Frozen $Out

Write-Host "== Licences of everything inside (they must travel with the program)"
Invoke-Native "collecting the licences" $VenvPython @((Join-Path $Root "packaging\collect_licenses.py"), "--internal", (Join-Path $Out "_internal"), "--out", (Join-Path $Out "licenses"))
Copy-Item (Join-Path $Root "LICENSE") $Out
Copy-Item (Join-Path $Out "licenses\THIRD_PARTY_NOTICES.md") $Out

@"
ThrashDash $Version for Windows
================================

Double-click thrashdash.exe to start it. A dashboard window opens (it uses Microsoft Edge, Chrome or Brave if you have one; Edge
comes with Windows). Closing the last dashboard window quits ThrashDash.

First, install the mod into BeamNG.drive. In a terminal (PowerShell or cmd), in this folder:

    .\thrashdash-cli.exe --install-mod

then remove your car in the game and spawn it again. thrashdash-cli.exe is the same program with a console window, for commands
that print something: --help, --version, --install-mod. Try it without the game first:

    .\thrashdash-cli.exe --simulate

Messages from thrashdash.exe are written to thrashdash.log in %LOCALAPPDATA%\ThrashDash.

The licences of everything in this folder are in the licenses folder; see THIRD_PARTY_NOTICES.md and LICENSE.
"@ | Set-Content -Encoding UTF8 (Join-Path $Out "README.txt")

Write-Host "== Making the zip"
$Zip = Join-Path $Dist "$Name.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path $Out -DestinationPath $Zip
$Hash = (Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLower()

Write-Host ""
Write-Host "Built $Zip" -ForegroundColor Green
Write-Host ("size   : {0:N1} MB" -f ((Get-Item $Zip).Length / 1MB))
Write-Host "sha256 : $Hash"
Write-Host "Try it : $Out\thrashdash-cli.exe --simulate"
