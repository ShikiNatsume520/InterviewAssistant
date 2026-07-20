param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$frontendIndex = Join-Path $projectRoot "frontend\dist\index.html"
$url = "http://127.0.0.1:$Port"

function Test-LocalPortInUse {
    param([int]$TargetPort)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync("127.0.0.1", $TargetPort)
        return $connect.Wait(300) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

Set-Location $projectRoot

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "[ERROR] .venv was not found. Complete 'First-time setup' in README." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $frontendIndex)) {
    Write-Host "[ERROR] frontend/dist was not found. Run:" -ForegroundColor Red
    Write-Host "        cd frontend"
    Write-Host "        npm install"
    Write-Host "        npm run build"
    exit 1
}

if (Test-LocalPortInUse -TargetPort $Port) {
    Write-Host "[ERROR] Port $Port is already in use. The demo may already be running: $url" -ForegroundColor Red
    exit 1
}

$sourcePath = Join-Path $projectRoot "src"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$sourcePath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $sourcePath
}

Write-Host ""
Write-Host "Interview Assistant Demo" -ForegroundColor Cyan
Write-Host "URL:  $url"
Write-Host "Stop: press Ctrl+C in this window"
Write-Host ""

$browserJob = $null
if (-not $NoBrowser) {
    $browserJob = Start-Job -ArgumentList $url -ScriptBlock {
        param($TargetUrl)
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                Invoke-WebRequest -Uri "$TargetUrl/health" -UseBasicParsing -TimeoutSec 1 | Out-Null
                Start-Process $TargetUrl
                return
            } catch {
                Start-Sleep -Milliseconds 250
            }
        }
    }
}

try {
    & $python -m uvicorn server.app:app --host 127.0.0.1 --port $Port --workers 1
} finally {
    if ($browserJob) {
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}
