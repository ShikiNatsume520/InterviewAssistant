param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 18765
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $projectRoot "demo.cmd"
$url = "http://127.0.0.1:$Port"

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

Write-Host "[probe] checking the occupied-port branch"
$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    $Port
)
$listener.Start()
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $occupiedOutput = & $launcher -NoBrowser -Port $Port 2>&1
    $occupiedExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousErrorAction
    $listener.Stop()
}

Assert-True ($occupiedExitCode -eq 1) "occupied port should return exit code 1"
Assert-True (
    ($occupiedOutput -join "`n").Contains("already in use")
) "occupied-port message was not emitted"

Write-Host "[probe] starting the real application"
$job = Start-Job -ArgumentList $launcher, $Port -ScriptBlock {
    param($LauncherPath, $ProbePort)
    & $LauncherPath -NoBrowser -Port $ProbePort
}

try {
    $healthy = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-WebRequest -Uri "$url/health" -UseBasicParsing -TimeoutSec 1
            if ($health.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    Assert-True $healthy "application did not become healthy within 15 seconds"

    $homeResponse = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
    Assert-True ($homeResponse.StatusCode -eq 200) "frontend root did not return HTTP 200"
    Assert-True (
        $homeResponse.Content.Contains('<div id="root"></div>')
    ) "frontend HTML was not served"

    Write-Host "[probe] health and frontend checks passed"
} finally {
    Stop-Job -Job $job -ErrorAction SilentlyContinue
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue

    $owner = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty OwningProcess
    if ($owner) {
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    }
}

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
        Write-Host "[probe] PASS: launcher behavior verified and port released"
        exit 0
    }
    Start-Sleep -Milliseconds 100
}

throw "port $Port remained occupied after probe cleanup"
