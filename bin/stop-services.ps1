param(
  [switch]$KillByPortFallback
)

$ErrorActionPreference = "SilentlyContinue"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path $repoRoot ".runtime\services.pids.json"

function Stop-IfAlive([int]$pid) {
  if ($pid -and (Get-Process -Id $pid -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $pid -Force
    Write-Host "Stopped PID: $pid"
  }
}

if (Test-Path $pidFile) {
  $data = Get-Content $pidFile -Raw | ConvertFrom-Json
  Stop-IfAlive ([int]$data.backend_pid)
  Stop-IfAlive ([int]$data.frontend_pid)
  Remove-Item $pidFile -Force
  Write-Host "Removed PID file."
} else {
  Write-Host "PID file not found."
}

if ($KillByPortFallback) {
  foreach ($port in @(8000, 5173)) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
      if ($c.OwningProcess) {
        Stop-Process -Id $c.OwningProcess -Force
        Write-Host "Stopped process on port $port, PID: $($c.OwningProcess)"
      }
    }
  }
}