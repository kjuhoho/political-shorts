<#
    Register a daily Windows Scheduled Task that runs the pipeline.
    Wrapper around `python -m political_shorts schedule add`.

    powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1 -At 07:30
    powershell -ExecutionPolicy Bypass -File .\scripts\register_task.ps1 -Remove
#>
param(
    [string]$At = "07:30",
    [switch]$NoCollect,
    [switch]$Remove,
    [switch]$Status
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$vpy = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { $vpy = "python" }

if ($Remove) {
    & $vpy -m political_shorts schedule remove
} elseif ($Status) {
    & $vpy -m political_shorts schedule status
} else {
    $args = @("-m", "political_shorts", "schedule", "add", "--at", $At)
    if ($NoCollect) { $args += "--no-collect" }
    & $vpy @args
}
