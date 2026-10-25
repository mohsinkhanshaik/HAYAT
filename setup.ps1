[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw 'Python was not found on PATH. Install a supported 64-bit Python, then rerun setup.ps1.'
}

& $PythonCommand.Source bootstrap.py
exit $LASTEXITCODE
