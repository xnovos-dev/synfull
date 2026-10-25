$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $Root 'frontend')
try { npm run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed' } } finally { Pop-Location }
$Python = Join-Path $Root '.venv/Scripts/python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }
& $Python -m compileall -q (Join-Path $Root 'backend/app')
if ($LASTEXITCODE -ne 0) { throw 'Backend compilation failed' }
