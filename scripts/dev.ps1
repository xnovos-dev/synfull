$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "$Name is required. $Hint" }
}
Require-Command 'python' 'Install Python 3.12+ and ensure it is on PATH.'
Require-Command 'node' 'Install a supported Node.js release and ensure it is on PATH.'
Require-Command 'npm' 'Install npm with Node.js and ensure it is on PATH.'
$PythonVersion = & python -c 'import sys; print(sys.version_info.major, sys.version_info.minor)'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($PythonVersion)) { throw 'Could not determine the Python version.' }
$VersionParts = $PythonVersion.Trim().Split(' ')
if ([int]$VersionParts[0] -lt 3 -or ([int]$VersionParts[0] -eq 3 -and [int]$VersionParts[1] -lt 10)) { throw "Python 3.10 or newer is required (found $PythonVersion)." }
$Venv = Join-Path $Root '.venv'
if (-not (Test-Path (Join-Path $Venv 'Scripts/python.exe'))) { & python -m venv $Venv; if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv' } }
$Py = Join-Path $Venv 'Scripts/python.exe'
& $Py -m pip install -r (Join-Path $Backend 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed' }
if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) { Push-Location $Frontend; try { npm install; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' } } finally { Pop-Location } }
# Resolve ports once so uvicorn and Vite's API proxy always agree, and fail early
# with a useful diagnostic instead of leaving one child to exit unnoticed.
$BackendPort = if ($env:SYNFULL_BIND_PORT) { [int]$env:SYNFULL_BIND_PORT } else { 8000 }
$FrontendPort = if ($env:SYNFULL_DEV_PORT) { [int]$env:SYNFULL_DEV_PORT } else { 5173 }
function Test-LoopbackPort([int]$Port) {
    $Probe = $null
    try {
        $Probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $Probe.Start()
        return $true
    } catch { return $false }
    finally { if ($null -ne $Probe) { $Probe.Stop() } }
}
function Test-SynfullApiPort([int]$Port) {
    try {
        $Response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($Response.StatusCode -ne 200) { return $false }
        $Status = Invoke-RestMethod -UseBasicParsing "http://127.0.0.1:$Port/api/v1/auth/status" -TimeoutSec 2
        $OpenApi = Invoke-RestMethod -UseBasicParsing "http://127.0.0.1:$Port/openapi.json" -TimeoutSec 2
        return ($Status -and $OpenApi.paths.PSObject.Properties.Name -contains '/api/v1/management/metrics/history')
    } catch { return $false }
}
$BackendAlreadyRunning = $false
if (-not (Test-LoopbackPort $BackendPort)) {
    if (Test-SynfullApiPort $BackendPort) {
        $BackendAlreadyRunning = $true
        Write-Host "Reusing the running Synfull API on port $BackendPort."
    } else {
        $BackendPort++
        while (-not (Test-LoopbackPort $BackendPort)) { $BackendPort++ }
    }
}
if (-not (Test-LoopbackPort $FrontendPort)) {
    $FrontendPort++
    while (-not (Test-LoopbackPort $FrontendPort)) { $FrontendPort++ }
}
if (-not $BackendAlreadyRunning -and -not (Test-LoopbackPort $BackendPort)) { throw "Could not find an available backend port." }
if (-not (Test-LoopbackPort $FrontendPort)) { throw "Could not find an available frontend port." }
$OriginalBindPort = $env:SYNFULL_BIND_PORT
$OriginalDevPort = $env:SYNFULL_DEV_PORT
$env:SYNFULL_BIND_PORT = "$BackendPort"
$env:SYNFULL_DEV_PORT = "$FrontendPort"
$BackendProcess = $null
$FrontendProcess = $null
try {
    if (-not $BackendAlreadyRunning) {
        $BackendProcess = Start-Process -FilePath $Py -ArgumentList @('-m','uvicorn','app.main:app','--app-dir',$Backend,'--host','127.0.0.1','--port',"$BackendPort",'--reload') -PassThru -NoNewWindow
    }
    # Launch Vite directly so the tracked process is the actual dashboard server,
    # not npm's short-lived wrapper process.
    $NodeCommand = Get-Command 'node.exe' -ErrorAction Stop
    $ViteEntry = Join-Path $Frontend 'node_modules/vite/bin/vite.js'
    if (-not (Test-Path $ViteEntry)) { throw "Could not locate Vite at $ViteEntry. Run npm install in the frontend directory." }
    $FrontendProcess = Start-Process -FilePath $NodeCommand.Source -ArgumentList @(('"{0}"' -f $ViteEntry),'--host','127.0.0.1','--port',"$FrontendPort") -WorkingDirectory $Frontend -PassThru -NoNewWindow
    # Wait for both HTTP servers before reporting ready; surface child exit codes if startup fails.
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        if ($null -ne $BackendProcess) { $BackendProcess.Refresh() }
        $FrontendProcess.Refresh()
        if (($null -ne $BackendProcess -and $BackendProcess.HasExited) -or $FrontendProcess.HasExited) {
            $BackendCode = if ($null -eq $BackendProcess) { 'reused' } elseif ($BackendProcess.HasExited) { $BackendProcess.ExitCode } else { 'running' }
            $FrontendCode = if ($FrontendProcess.HasExited) { $FrontendProcess.ExitCode } else { 'running' }
            throw "A development server failed during startup (backend exit: $BackendCode; frontend exit: $FrontendCode). Check port settings and try again."
        }
        try {
            $BackendReady = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$BackendPort/health" -TimeoutSec 1).StatusCode -eq 200
            $FrontendReady = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$FrontendPort/" -TimeoutSec 1).StatusCode -eq 200
            if ($BackendReady -and $FrontendReady) { $Ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $Ready) { throw 'Development servers did not become ready within 30 seconds.' }
    Write-Host 'Synfull development servers are running:' -ForegroundColor Green
    Write-Host "  Dashboard: http://127.0.0.1:$FrontendPort"
    Write-Host "  API docs:  http://127.0.0.1:$BackendPort/docs"
    Write-Host 'Press Ctrl+C to stop both servers.'
    while (($null -eq $BackendProcess -or -not $BackendProcess.HasExited) -and -not $FrontendProcess.HasExited) {
        Start-Sleep -Seconds 1
        if ($null -ne $BackendProcess) { $BackendProcess.Refresh() }
        $FrontendProcess.Refresh()
    }
    if (($null -ne $BackendProcess -and $BackendProcess.HasExited) -or $FrontendProcess.HasExited) {
        $BackendCode = if ($null -eq $BackendProcess) { 'reused' } elseif ($BackendProcess.HasExited) { $BackendProcess.ExitCode } else { 'running' }
        $FrontendCode = if ($FrontendProcess.HasExited) { $FrontendProcess.ExitCode } else { 'running' }
        throw "A development server exited unexpectedly (backend exit: $BackendCode; frontend exit: $FrontendCode)."
    }
} finally {
    foreach ($Process in @($FrontendProcess, $BackendProcess)) {
        if ($null -ne $Process -and -not $Process.HasExited) {
            & taskkill.exe /PID $Process.Id /T /F 2>$null | Out-Null
        }
    }
    if ($null -eq $OriginalBindPort) { Remove-Item Env:SYNFULL_BIND_PORT -ErrorAction SilentlyContinue } else { $env:SYNFULL_BIND_PORT = $OriginalBindPort }
    if ($null -eq $OriginalDevPort) { Remove-Item Env:SYNFULL_DEV_PORT -ErrorAction SilentlyContinue } else { $env:SYNFULL_DEV_PORT = $OriginalDevPort }
}
