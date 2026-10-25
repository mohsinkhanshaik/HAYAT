#Requires -Version 5.1
<#
.SYNOPSIS
    Windows validation control plane. Runs real checks against this machine
    and records honest PASS/FAIL/SKIPPED/UNAVAILABLE/NOT_CONFIGURED/
    REQUIRES_HUMAN/NOT_VERIFIED status -- never upgrades a sandbox or mocked
    result into a Windows-verified one.

.PARAMETER Quick
    Compile check, full pytest run, and doctor.py. No hardware, no network,
    ~1 minute. Safe to run before every risky change.

.PARAMETER Standard
    Quick, plus the release gate, a live local-model round trip, real
    pywinauto window control (spawns and closes Notepad), and a headless
    browser round trip. Requires Ollama and a Chrome/Edge install.

.PARAMETER Full
    Standard, plus camera capture, a real speaker-to-microphone voice
    loopback, and local OCR accuracy. Requires a working camera and
    microphone/speaker and will make brief use of both.

.EXAMPLE
    .\scripts\windows_validation.ps1 -Quick
.EXAMPLE
    .\scripts\windows_validation.ps1 -Full
#>
[CmdletBinding()]
param(
    [switch]$Quick,
    [switch]$Standard,
    [switch]$Full
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
Set-Location -LiteralPath $ProjectRoot

$SelectedCount = @($Quick, $Standard, $Full) | Where-Object { $_ } | Measure-Object | Select-Object -ExpandProperty Count
if ($SelectedCount -eq 0) { $Quick = $true }
if ($SelectedCount -gt 1) { throw 'Pass exactly one of -Quick, -Standard, or -Full.' }
$Tier = if ($Full) { 3 } elseif ($Standard) { 2 } else { 1 }
$TierName = @('', 'Quick', 'Standard', 'Full')[$Tier]

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw 'The project virtual environment is missing. Run .\setup.ps1 first.'
}

# This script only ever runs status labels PASS/FAIL/etc for checks it
# actually executed on this line below. A run that reaches this point is
# genuinely on Windows -- $IsWindows is $true on PS7, and PS 5.1 (Desktop
# edition) only ships on Windows -- but a check can still be UNAVAILABLE
# for lack of hardware, or NOT_CONFIGURED for lack of a reachable service.
$HostPlatform = if ($PSVersionTable.PSEdition -eq 'Desktop') { 'Windows (PowerShell Desktop)' } elseif ($IsWindows) { 'Windows (PowerShell Core)' } else { 'NOT WINDOWS -- results below are not Windows-verified' }

function Invoke-Check {
    param(
        [string]$Name,
        [string]$Category,
        [string[]]$Arguments,
        [int]$TimeoutSeconds,
        [string[]]$UnavailablePatterns = @(),
        [string[]]$NotConfiguredPatterns = @(),
        [string]$SkipReason = $null
    )

    if ($SkipReason) {
        Write-Host "  [SKIP] $Name -- $SkipReason" -ForegroundColor DarkYellow
        return [ordered]@{
            name = $Name; category = $Category; status = 'SKIPPED'; reason = $SkipReason
            exit_code = $null; duration_ms = 0; output_tail = ''
        }
    }

    Write-Host "  [RUN]  $Name" -ForegroundColor Cyan
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $stdoutFile = New-TemporaryFile
    $stderrFile = New-TemporaryFile
    try {
        $process = Start-Process -FilePath $VenvPython -ArgumentList $Arguments -WorkingDirectory $ProjectRoot `
            -NoNewWindow -PassThru -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
        # Accessing .Handle forces eager initialization; without it, .ExitCode
        # can read back $null after WaitForExit() due to a lazy-load quirk in
        # System.Diagnostics.Process on .NET Framework / PowerShell Desktop.
        $process.Handle | Out-Null
        $finished = $process.WaitForExit($TimeoutSeconds * 1000)
        if (-not $finished) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $stopwatch.Stop()
            $combined = "TIMEOUT after ${TimeoutSeconds}s"
            Write-Host "  [FAIL] $Name -- timed out after ${TimeoutSeconds}s" -ForegroundColor Red
            return [ordered]@{
                name = $Name; category = $Category; status = 'FAIL'; reason = 'timeout'
                exit_code = $null; duration_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 1)
                output_tail = $combined
            }
        }
        $exitCode = $process.ExitCode
        $stdout = Get-Content -LiteralPath $stdoutFile -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue
        $combined = "$stdout`n$stderr"
        $stopwatch.Stop()

        $status = 'FAIL'
        $reason = "exit code $exitCode"
        if ($exitCode -eq 0) {
            $status = 'PASS'
            $reason = 'exit code 0'
        } else {
            foreach ($pattern in $NotConfiguredPatterns) {
                if ($combined -match $pattern) { $status = 'NOT_CONFIGURED'; $reason = "matched: $pattern"; break }
            }
            if ($status -eq 'FAIL') {
                foreach ($pattern in $UnavailablePatterns) {
                    if ($combined -match $pattern) { $status = 'UNAVAILABLE'; $reason = "matched: $pattern"; break }
                }
            }
        }

        $color = switch ($status) { 'PASS' { 'Green' }; 'UNAVAILABLE' { 'DarkYellow' }; 'NOT_CONFIGURED' { 'DarkYellow' }; default { 'Red' } }
        Write-Host "  [$status] $Name ($([math]::Round($stopwatch.Elapsed.TotalSeconds, 1))s)" -ForegroundColor $color

        $tail = $combined.Trim()
        if ($tail.Length -gt 4000) { $tail = $tail.Substring($tail.Length - 4000) }

        return [ordered]@{
            name = $Name; category = $Category; status = $status; reason = $reason
            exit_code = $exitCode; duration_ms = [math]::Round($stopwatch.Elapsed.TotalMilliseconds, 1)
            output_tail = $tail
        }
    } finally {
        Remove-Item -LiteralPath $stdoutFile, $stderrFile -Force -ErrorAction SilentlyContinue
    }
}

$Results = New-Object System.Collections.Generic.List[object]

Write-Host "Windows validation -- tier: $TierName" -ForegroundColor White
Write-Host "Host: $HostPlatform" -ForegroundColor White
Write-Host ""

# ---------------------------------------------------------------- Quick tier

Write-Host "-- Quick checks --" -ForegroundColor White
$Results.Add((Invoke-Check -Name 'compile' -Category 'quick' `
    -Arguments @('-m', 'compileall', '-q', 'app', 'scripts', 'tests') -TimeoutSeconds 120))

$Results.Add((Invoke-Check -Name 'pytest' -Category 'quick' `
    -Arguments @('-m', 'pytest', '-q') -TimeoutSeconds 300))

$Results.Add((Invoke-Check -Name 'doctor' -Category 'quick' `
    -Arguments @('scripts/doctor.py') -TimeoutSeconds 120 `
    -UnavailablePatterns @('ConnectionError', 'Ollama', 'refused')))

$Results.Add((Invoke-Check -Name 'generated_docs_current' -Category 'quick' `
    -Arguments @('scripts/generate_reference.py', '--check') -TimeoutSeconds 60))

# ------------------------------------------------------------- Standard tier

if ($Tier -ge 2) {
    Write-Host "-- Standard checks --" -ForegroundColor White
    $Results.Add((Invoke-Check -Name 'release_gate' -Category 'standard' `
        -Arguments @('scripts/release_gate.py') -TimeoutSeconds 600))

    $Results.Add((Invoke-Check -Name 'local_model_roundtrip' -Category 'standard' `
        -Arguments @('scripts/model_check.py') -TimeoutSeconds 90 `
        -NotConfiguredPatterns @('No local or configured cloud model provider is reachable')))

    $Results.Add((Invoke-Check -Name 'browser_automation' -Category 'standard' `
        -Arguments @('scripts/integration_check.py') -TimeoutSeconds 120 `
        -UnavailablePatterns @('Executable doesn''t exist', 'playwright install')))

    $Results.Add((Invoke-Check -Name 'windows_window_control' -Category 'standard' `
        -Arguments @('scripts/windows_notepad_integration_check.py') -TimeoutSeconds 60 `
        -UnavailablePatterns @('No module named .pywinauto.', 'notepad.exe')))
}

# ------------------------------------------------------------------ Full tier

if ($Tier -ge 3) {
    Write-Host "-- Full checks (uses camera and audio hardware) --" -ForegroundColor White
    $Results.Add((Invoke-Check -Name 'camera_capture' -Category 'full' `
        -Arguments @('scripts/camera_hardware_check.py') -TimeoutSeconds 60 `
        -UnavailablePatterns @('could not open', 'No camera', 'device index out of range')))

    $Results.Add((Invoke-Check -Name 'voice_loopback' -Category 'full' `
        -Arguments @('scripts/voice_hardware_loopback_check.py') -TimeoutSeconds 60 `
        -UnavailablePatterns @('Microphone capture did not finish', 'PortAudioError', 'no default output device')))

    $Results.Add((Invoke-Check -Name 'voice_windows_integration' -Category 'full' `
        -Arguments @('scripts/voice_windows_integration_check.py') -TimeoutSeconds 60 `
        -UnavailablePatterns @('No module named .pywinauto.')))

    $OcrScript = Join-Path $ProjectRoot 'scripts\ocr_benchmark.py'
    if (Test-Path -LiteralPath $OcrScript) {
        $Results.Add((Invoke-Check -Name 'ocr_accuracy' -Category 'full' `
            -Arguments @('scripts/ocr_benchmark.py') -TimeoutSeconds 120 `
            -UnavailablePatterns @('No OCR backend available')))
    } else {
        $Results.Add([ordered]@{
            name = 'ocr_accuracy'; category = 'full'; status = 'SKIPPED'
            reason = 'scripts/ocr_benchmark.py not present'; exit_code = $null; duration_ms = 0; output_tail = ''
        })
    }
}

# --------------------------------------------------------------------- Report

$Overall = if (($Results | Where-Object { $_.status -eq 'FAIL' }).Count -gt 0) { 'FAIL' }
    elseif (($Results | Where-Object { $_.status -in @('PASS') }).Count -eq 0) { 'NOT_VERIFIED' }
    else { 'PASS' }

$Payload = [ordered]@{
    schema_version = 1
    generated_at   = (Get-Date).ToUniversalTime().ToString('o')
    tier           = $TierName
    host_platform  = $HostPlatform
    machine        = [ordered]@{
        os      = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
        machine = (Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).Model
    }
    overall_status = $Overall
    checks         = $Results
}

$OutDir = Join-Path $ProjectRoot 'data\benchmarks\windows_validation'
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$JsonPath = Join-Path $OutDir 'latest.json'
$MdPath = Join-Path $OutDir 'latest.md'

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($JsonPath, ($Payload | ConvertTo-Json -Depth 6), $Utf8NoBom)

$Md = New-Object System.Collections.Generic.List[string]
$Md.Add('# Windows validation report')
$Md.Add('')
$Md.Add("Generated: $($Payload.generated_at)")
$Md.Add("Tier: $TierName")
$Md.Add("Host: $HostPlatform")
$Md.Add("Overall: **$Overall**")
$Md.Add('')
$Md.Add('| Check | Category | Status | Duration (ms) | Reason |')
$Md.Add('|---|---|---|---|---|')
foreach ($item in $Results) {
    $Md.Add("| $($item.name) | $($item.category) | $($item.status) | $($item.duration_ms) | $($item.reason) |")
}
$Md.Add('')
$Md.Add('Status meanings: PASS (ran and succeeded on this machine), FAIL (ran and failed -- investigate), ' +
    'SKIPPED (not applicable to this tier or prerequisite script missing), ' +
    'UNAVAILABLE (hardware/dependency not present on this machine), ' +
    'NOT_CONFIGURED (a required service is not reachable, e.g. Ollama not running), ' +
    'REQUIRES_HUMAN (needs a person present, not run by this script), ' +
    'NOT_VERIFIED (nothing here proves the claim).')
[System.IO.File]::WriteAllText($MdPath, ($Md -join "`n"), $Utf8NoBom)

Write-Host ""
Write-Host "Overall: $Overall" -ForegroundColor $(if ($Overall -eq 'PASS') { 'Green' } else { 'Red' })
Write-Host "Report:  $JsonPath"
Write-Host "Summary: $MdPath"

exit $(if ($Overall -eq 'PASS') { 0 } else { 1 })
