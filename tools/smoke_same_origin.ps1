# Smoke-test для same-origin стека: Cloudflare Tunnel → nginx (80) → UI_v2 (8080/8081)
#
# Перевіряє локально:
# - HTTP: / має віддавати HTML (або принаймні 200)
# - HTTP: /smc-viewer/snapshot?symbol=XAUUSD має віддавати JSON
#
# Запуск (PowerShell):
#   .\tools\smoke_same_origin.ps1

$ErrorActionPreference = "Stop"

function Write-Ok($msg) { Write-Host "[OK]  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }

$base = "http://127.0.0.1:80"

# 1) Головна сторінка
try {
    $resp = Invoke-WebRequest -Uri "$base/" -Method GET -UseBasicParsing -TimeoutSec 10
    $status = [int]$resp.StatusCode
    if ($status -ge 200 -and $status -lt 300) {
        Write-Ok "HTTP / відповів $status"
    }
    else {
        Write-Warn "HTTP / відповів $status (очікували 2xx)"
    }

    $ct = ($resp.Headers["Content-Type"] | Select-Object -First 1)
    if ($ct -and $ct.ToLower().Contains("text/html")) {
        Write-Ok "Content-Type схожий на HTML ($ct)"
    }
    else {
        Write-Warn "Content-Type не схожий на HTML: $ct"
    }
}
catch {
    Write-Fail "HTTP / не відкривається: $($_.Exception.Message)"
    Write-Host "Підказка: перевірте, що nginx у Docker слухає 80 (docker compose ps)" -ForegroundColor DarkGray
    exit 1
}

# 2) Snapshot JSON
try {
    $snapUrl = "$base/smc-viewer/snapshot?symbol=XAUUSD"
    $resp2 = Invoke-WebRequest -Uri $snapUrl -Method GET -UseBasicParsing -TimeoutSec 10
    $status2 = [int]$resp2.StatusCode
    if ($status2 -ge 200 -and $status2 -lt 300) {
        Write-Ok "HTTP snapshot відповів $status2"
    }
    else {
        Write-Warn "HTTP snapshot відповів $status2 (очікували 2xx)"
    }

    $body = $resp2.Content
    try {
        $null = $body | ConvertFrom-Json
        Write-Ok "Snapshot схожий на валідний JSON"
    }
    catch {
        Write-Fail "Snapshot не парситься як JSON"
        Write-Host "Початок відповіді: $($body.Substring(0, [Math]::Min(200, $body.Length)))" -ForegroundColor DarkGray
        exit 1
    }
}
catch {
    Write-Fail "HTTP snapshot не відкривається: $($_.Exception.Message)"
    Write-Host "Підказка: перевірте, що UI_v2 HTTP (8080) запущений і nginx проксить /smc-viewer/snapshot" -ForegroundColor DarkGray
    exit 1
}

Write-Host ""
Write-Host "Підказка для WS перевірки:" -ForegroundColor Cyan
Write-Host "- Локально: ws://127.0.0.1/smc-viewer/stream?symbol=XAUUSD" -ForegroundColor Cyan
Write-Host "- Публічно: wss://aione-smc.com/smc-viewer/stream?symbol=XAUUSD" -ForegroundColor Cyan
