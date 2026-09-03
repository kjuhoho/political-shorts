<#
    political-shorts installer / bootstrapper for Windows 11.

    Usage (from the project folder):
        powershell -ExecutionPolicy Bypass -File .\install.ps1
        powershell -ExecutionPolicy Bypass -File .\install.ps1 -WithPublish -InstallFfmpeg

    It will:
      * create .\.venv
      * install runtime deps (and optionally the publish extras)
      * install this project in editable mode
      * create .env from .env.example if it does not exist
      * optionally install ffmpeg via winget
      * run scripts\smoke_test.py
#>
[CmdletBinding()]
param(
    [switch]$WithPublish,
    [switch]$InstallFfmpeg,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "== political-shorts installer ==" -ForegroundColor Cyan
Write-Host "project: $root"

# --- 1. Python ------------------------------------------------------------
$py = $null
foreach ($cand in @("py -3", "python", "python3")) {
    try {
        $parts = $cand.Split(" ")
        $v = & $parts[0] $parts[1..($parts.Length-1)] --version 2>&1
        if ($LASTEXITCODE -eq 0) { $py = $cand; break }
    } catch { }
}
if (-not $py) { throw "Python 3.10+ not found on PATH. Install from https://www.python.org/downloads/ and re-run." }
Write-Host "python  : $py ($v)"

# --- 2. venv -----------------------------------------------------------
$venv = Join-Path $root ".venv"
if (-not (Test-Path $venv)) {
    Write-Host "creating virtual environment ..." -ForegroundColor Yellow
    $parts = $py.Split(" ")
    & $parts[0] $parts[1..($parts.Length-1)] -m venv $venv
}
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) { throw "venv python missing at $vpy" }

# --- 3. deps ---------------------------------------------------------
Write-Host "upgrading pip ..." -ForegroundColor Yellow
& $vpy -m pip install --upgrade pip wheel setuptools | Out-Null

Write-Host "installing runtime dependencies ..." -ForegroundColor Yellow
& $vpy -m pip install -r (Join-Path $root "requirements.txt")

if ($WithPublish) {
    Write-Host "installing publish extras ..." -ForegroundColor Yellow
    & $vpy -m pip install -r (Join-Path $root "requirements-publish.txt")
}

Write-Host "installing project (editable) ..." -ForegroundColor Yellow
& $vpy -m pip install -e $root

& $vpy -m pip install pytest | Out-Null

# --- 4. .env -------------------------------------------------------
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Write-Host "created .env from .env.example  (edit it before enabling publishing)" -ForegroundColor Green
} else {
    Write-Host ".env already exists — left untouched"
}

# --- 5. ffmpeg ------------------------------------------------
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff -and $InstallFfmpeg) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "installing ffmpeg via winget ..." -ForegroundColor Yellow
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
        Write-Host "NOTE: open a new terminal so PATH picks up ffmpeg." -ForegroundColor Yellow
    } else {
        Write-Host "winget not available; install ffmpeg manually from https://www.gyan.dev/ffmpeg/builds/" -ForegroundColor Yellow
    }
} elseif (-not $ff) {
    Write-Host "ffmpeg not found. Video rendering will be skipped until you install it:" -ForegroundColor Yellow
    Write-Host "    winget install --id Gyan.FFmpeg -e" -ForegroundColor Yellow
} else {
    Write-Host "ffmpeg  : $($ff.Source)"
}

# --- 6. smoke test -------------------------------------------
if (-not $SkipSmokeTest) {
    Write-Host "`n== running smoke test ==" -ForegroundColor Cyan
    & $vpy (Join-Path $root "scripts\smoke_test.py")
    $smoke = $LASTEXITCODE
} else {
    $smoke = 0
}

Write-Host "`n== done ==" -ForegroundColor Cyan
Write-Host "next steps:"
Write-Host "  1. edit .env"
Write-Host "  2. .\.venv\Scripts\python.exe -m political_shorts doctor"
Write-Host "  3. .\.venv\Scripts\python.exe -m political_shorts run --no-collect   (after a collect)"
Write-Host "  4. .\.venv\Scripts\python.exe -m political_shorts dashboard   -> http://127.0.0.1:8765"
Write-Host "  5. .\.venv\Scripts\python.exe -m political_shorts schedule add --at 07:30"
if ($smoke -ne 0) {
    Write-Host "`nsmoke test reported issues — see output above. Core install may still be fine." -ForegroundColor Yellow
    exit $smoke
}
