[CmdletBinding()]
param(
    [switch]$Voice,
    [switch]$Combined,
    [switch]$Tray,
    [switch]$Gui,
    [switch]$CliOnly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AgentArguments
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot
$LaunchArguments = @()
if ($Voice) { $LaunchArguments += '--voice' }
if ($Gui) { $LaunchArguments += '--gui' }
if ($CliOnly) { $LaunchArguments += '--cli-only' }
if ($Combined) { $LaunchArguments += '--combined' }
if ($Tray) { $LaunchArguments += '--tray' }
$LaunchArguments += $AgentArguments
# HAYAT's own Python (scripts/make_portable.py) first, so it runs by itself.
$PythonPath = Join-Path $ProjectRoot 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $PythonPath)) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
& $PythonPath run.py @LaunchArguments
exit $LASTEXITCODE
