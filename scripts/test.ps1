$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv/Scripts/python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }
Push-Location (Join-Path $Root 'backend')
try { & $Python -m pytest tests; if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' } } finally { Pop-Location }
Push-Location (Join-Path $Root 'frontend')
try { npm run lint; if ($LASTEXITCODE -ne 0) { throw 'Frontend type check failed' } } finally { Pop-Location }
