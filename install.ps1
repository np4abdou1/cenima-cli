$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/np4abdou1/cenima-cli.git"
$InstallRoot = Join-Path $env:LOCALAPPDATA "cenima-cli"
$VenvDir = Join-Path $InstallRoot "venv"
$SrcDir = Join-Path $InstallRoot "src"

Write-Host "==> Checking Python..." -ForegroundColor Cyan
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $Python) {
    Write-Error "Python 3.9+ is required. Install from https://www.python.org/downloads/"
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
if (-not (Test-Path $VenvDir)) {
    Write-Host "==> Creating virtual environment in $VenvDir" -ForegroundColor Cyan
    & $Python.Source -m venv $VenvDir
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
$ScriptsDir = Join-Path $VenvDir "Scripts"

& $VenvPython -m pip install --upgrade pip | Out-Null

if ((Test-Path ".\pyproject.toml") -and (Select-String -Path ".\pyproject.toml" -Pattern 'name = "cenima-cli"' -Quiet)) {
    Write-Host "==> Installing cenima-cli from local directory" -ForegroundColor Cyan
    & $VenvPip install --upgrade ".[download]"
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Error "git is required when installing remotely."
    }
    if (Test-Path (Join-Path $SrcDir ".git")) {
        Write-Host "==> Updating source in $SrcDir" -ForegroundColor Cyan
        git -C $SrcDir pull --ff-only origin main
    } else {
        Write-Host "==> Cloning cenima-cli into $SrcDir" -ForegroundColor Cyan
        git clone --depth 1 $RepoUrl $SrcDir
    }
    & $VenvPip install --upgrade "$SrcDir[download]"
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$ScriptsDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$ScriptsDir", "User")
    Write-Host "==> Added $ScriptsDir to User PATH (restart terminal to take effect)" -ForegroundColor Yellow
}

Write-Host "==> Done! Run 'cinema-cli' (or 'cenima-cli --doctor') to start." -ForegroundColor Green
