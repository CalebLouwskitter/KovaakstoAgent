$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$codexPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

if (Test-Path -LiteralPath $codexPython) {
    $pythonExe = $codexPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = (Get-Command python).Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonExe = (Get-Command py).Source
} else {
    throw 'Python 3 was not found. Install Python 3.11+ or run this project through Codex.'
}

Set-Location -LiteralPath $projectRoot
& $pythonExe -m app.server

