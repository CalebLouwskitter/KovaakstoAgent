<#
.SYNOPSIS
Builds the Kovaak Agent desktop app and its Windows installer.

.DESCRIPTION
Creates build\venv, installs requirements-desktop.txt, runs the tests, bundles the
app with PyInstaller into dist\KovaakAgent, then compiles dist\KovaakAgent-Setup-<version>.exe
with Inno Setup 6 (ISCC.exe on PATH or in its default install folders).

.EXAMPLE
./packaging/build.ps1
./packaging/build.ps1 -Python 'py -3.12' -SkipInstaller
#>
param(
    [string]$Python = 'python',
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    # Tools log progress on stderr; judge them by exit code, not by stderr output.
    $ErrorActionPreference = 'Continue'
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

$venv = Join-Path $root 'build\venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    $launcher, $launcherArgs = $Python -split ' '
    Invoke-Checked $launcher (@($launcherArgs | Where-Object { $_ }) + @('-m', 'venv', $venv))
}
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', 'requirements-desktop.txt')

$version = (& $venvPython -c 'import app; print(app.__version__)').Trim()
Write-Host "Building Kovaak Agent $version"

if (-not $SkipTests) {
    Invoke-Checked $venvPython @('-m', 'unittest', 'discover', '-s', 'tests')
}

Invoke-Checked $venvPython @('-m', 'PyInstaller', '--noconfirm', '--clean',
    '--distpath', 'dist', '--workpath', 'build\pyinstaller', 'packaging\kovaak-agent.spec')

if ($SkipInstaller) {
    Write-Host "App folder: dist\KovaakAgent"
    return
}

$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
if (-not $iscc) {
    throw 'Inno Setup 6 was not found. Install it (winget install JRSoftware.InnoSetup) or pass -SkipInstaller.'
}
Invoke-Checked $iscc @("/DAppVersion=$version", 'packaging\installer.iss')
Write-Host "Installer: dist\KovaakAgent-Setup-$version.exe"
