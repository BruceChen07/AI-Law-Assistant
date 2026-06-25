param(
  [string]$ReportPath = "",
  [switch]$IncludeLocalSmoke,
  [string]$LocalMainApiBase = "http://127.0.0.1:11434/v1",
  [string]$LocalSmallApiBase = "http://127.0.0.1:11434/v1",
  [string]$CloudApiBase = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $ReportPath) {
  $ReportPath = Join-Path $repoRoot "plan\edge-llm-cpu-local-regression-report.md"
}
$ReportPath = [System.IO.Path]::GetFullPath($ReportPath)
$reportDir = Split-Path -Parent $ReportPath
New-Item -ItemType Directory -Path $reportDir -Force | Out-Null

$testFiles = @(
  "tests/test_llm_router.py",
  "tests/test_llm_local_mode.py",
  "tests/test_json_guard.py",
  "tests/test_local_llm_fallback.py",
  "tests/test_tax_contract_parser.py",
  "tests/test_tax_matcher.py",
  "tests/test_tax_risk.py",
  "tests/test_memory_pipeline_fallback.py",
  "tests/test_contract_audit_memory_mode.py",
  "tests/test_contract_audit.py"
)

Push-Location $repoRoot
try {
  $pytestArgs = @("-m", "pytest") + $testFiles
  $pytestOutput = (& python $pytestArgs 2>&1 | Tee-Object -Variable PytestLines | Out-String)
  $pytestExitCode = $LASTEXITCODE
} finally {
  Pop-Location
}

$smokeMainStatus = "skipped"
$smokeSmallStatus = "skipped"
$smokeCloudStatus = "skipped"
$smokeNotes = @()

function Test-ChatCompletionEndpoint([string]$ApiBase, [string]$Model) {
  $uri = ($ApiBase.TrimEnd("/") + "/chat/completions")
  $body = @{
    model = $Model
    messages = @(
      @{ role = "system"; content = "Return ONLY JSON." },
      @{ role = "user"; content = "Return JSON: {`"ok`": true}" }
    )
    temperature = 0
    max_tokens = 32
  } | ConvertTo-Json -Depth 8
  Invoke-RestMethod -Uri $uri -Method Post -ContentType "application/json" -Body $body -TimeoutSec 20
}

if ($IncludeLocalSmoke) {
  try {
    $null = Test-ChatCompletionEndpoint -ApiBase $LocalMainApiBase -Model "qwen3.6:27b"
    $smokeMainStatus = "passed"
  } catch {
    $smokeMainStatus = "failed"
    $smokeNotes += "Local main smoke failed: $($_.Exception.Message)"
  }

  try {
    $null = Test-ChatCompletionEndpoint -ApiBase $LocalSmallApiBase -Model "llama3.2:3b"
    $smokeSmallStatus = "passed"
  } catch {
    $smokeSmallStatus = "failed"
    $smokeNotes += "Local small smoke failed: $($_.Exception.Message)"
  }

  if ($CloudApiBase) {
    try {
      $null = Test-ChatCompletionEndpoint -ApiBase $CloudApiBase -Model "gpt-4o-mini"
      $smokeCloudStatus = "passed"
    } catch {
      $smokeCloudStatus = "failed"
      $smokeNotes += "Cloud smoke failed: $($_.Exception.Message)"
    }
  }
}

$overallStatus = if ($pytestExitCode -eq 0) { "passed" } else { "failed" }
$timestamp = (Get-Date).ToString("s")
$machine = [System.Environment]::MachineName

$lines = @(
  "# Edge LLM CPU Local Regression Report",
  "",
  "- Generated At: $timestamp",
  "- Machine: $machine",
  "- Repository: $repoRoot",
  "- Overall Status: $overallStatus",
  "- Pytest Exit Code: $pytestExitCode",
  "",
  "## Test Scope",
  "",
  '```text',
  ($testFiles -join "`n"),
  '```',
  "",
  "## Pytest Command",
  "",
  '```powershell',
  "python -m pytest $($testFiles -join " ")",
  '```',
  "",
  "## Pytest Output",
  "",
  '```text',
  ($pytestOutput.TrimEnd()),
  '```',
  "",
  "## Smoke Check",
  "",
  "- Include Local Smoke: $([bool]$IncludeLocalSmoke)",
  "- Local Main API: $LocalMainApiBase",
  "- Local Small API: $LocalSmallApiBase",
  "- Cloud API: $(if ($CloudApiBase) { $CloudApiBase } else { "(not provided)" })",
  "- Local Main Result: $smokeMainStatus",
  "- Local Small Result: $smokeSmallStatus",
  "- Cloud Result: $smokeCloudStatus",
  "",
  "## Smoke Notes",
  ""
)

if ($smokeNotes.Count -gt 0) {
  $lines += ($smokeNotes | ForEach-Object { "- $_" })
} else {
  $lines += "- None"
}

$markdown = ($lines -join "`r`n")

Set-Content -Path $ReportPath -Value $markdown -Encoding UTF8

Write-Host "Regression report written to: $ReportPath"
Write-Host "Pytest exit code: $pytestExitCode"
if ($pytestExitCode -ne 0) {
  exit $pytestExitCode
}
