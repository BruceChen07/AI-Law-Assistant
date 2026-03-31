param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$runtimeDir = Join-Path $repoRoot ".runtime"
$pidFile = Join-Path $runtimeDir "services.pids.json"

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

if (Test-Path $pidFile) {
  $existing = Get-Content $pidFile -Raw | ConvertFrom-Json
  $alive = @()
  foreach ($p in @($existing.backend_pid, $existing.frontend_pid)) {
    if ($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) { $alive += $p }
  }
  if ($alive.Count -gt 0) {
    Write-Host "Services seem running. Use stop-services.ps1 first. Alive PIDs: $($alive -join ', ')"
    exit 1
  } else {
    Remove-Item $pidFile -Force
  }
}

$venvCandidates = @(
  (Join-Path $repoRoot "app\.venv\Scripts\Activate.ps1")
)
$venvActivate = $null
foreach ($candidate in $venvCandidates) {
  if (Test-Path $candidate) {
    $venvActivate = $candidate
    break
  }
}
if (-not $venvActivate) {
  Write-Host "Virtual environment not found. Checked:"
  foreach ($candidate in $venvCandidates) { Write-Host " - $candidate" }
  Write-Host "Please create one, e.g. python -m venv app\.venv"
  exit 1
}

$backendCmd = "Set-Location '$repoRoot'; & '$venvActivate'; `$env:APP_PORT='$BackendPort'; python -m app.main"
$frontendCmd = "Set-Location '$repoRoot\web'; npm run dev -- --host 0.0.0.0 --port $FrontendPort"

$backendProc = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoLogo","-NoExit","-ExecutionPolicy","Bypass","-Command",$backendCmd -PassThru
Start-Sleep -Milliseconds 800
$frontendProc = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoLogo","-NoExit","-ExecutionPolicy","Bypass","-Command",$frontendCmd -PassThru

$data = [ordered]@{
  backend_pid = $backendProc.Id
  frontend_pid = $frontendProc.Id
  backend_port = $BackendPort
  frontend_port = $FrontendPort
  started_at = (Get-Date).ToString("s")
} | ConvertTo-Json

Set-Content -Path $pidFile -Value $data -Encoding UTF8

Write-Host "Started backend PID: $($backendProc.Id) at http://localhost:$BackendPort"
Write-Host "Started frontend PID: $($frontendProc.Id) at http://localhost:$FrontendPort"
Write-Host "PID file: $pidFile"
