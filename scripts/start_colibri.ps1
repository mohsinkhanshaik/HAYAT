# Run the verified optional Colibri candidate, separate from HAYAT's default.
param(
    [ValidateSet('cpu', 'vulkan')][string]$Backend = 'cpu',
    [ValidateRange(1024, 65535)][int]$Port = 8000,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.venv/Scripts/python.exe'
$launcher = Join-Path $workspace 'runtime/colibri-v2.0.0/coli'
$model = Join-Path $workspace 'models/colibri/qwen36-35b-a3b-i4-gs64'
foreach ($required in @($python, $launcher, (Join-Path $model 'model-globals.safetensors'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing local installation file: $required" }
}
if (-not $CheckOnly) {
    foreach ($endpoint in @('http://127.0.0.1:11435/api/ps', 'http://127.0.0.1:11434/api/ps')) {
        $resident = $null
        try { $resident = Invoke-RestMethod -Uri $endpoint -TimeoutSec 2 } catch { }
        if ($resident.models | Where-Object { $_.size -gt 4GB }) {
            throw 'A large Ollama model is loaded. Unload it before starting the Colibri comparison model.'
        }
    }
    $free = & $python -c 'import psutil; print(psutil.virtual_memory().available)'
    $minimum = if ($Backend -eq 'vulkan') { 23GB } else { 16GB }
    if ($LASTEXITCODE -ne 0 -or [long]$free -lt $minimum) {
        throw "This tested Colibri profile needs at least $($minimum / 1GB) GiB available RAM before startup."
    }
}
$values = @{
    COLI_CUDA = '0'
    COLI_VULKAN = $(if ($Backend -eq 'vulkan') { '1' } else { '0' })
    COLI_VK_TIER_GB = '3'
    OMP_NUM_THREADS = '8'
    COLI_USAGE = (Join-Path $workspace 'data/cache/colibri-full-usage.bin')
}
$previous = @{}
foreach ($key in $values.Keys) { $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
$exitCode = 1
try {
    foreach ($key in $values.Keys) { [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process') }
    $gpu = if ($Backend -eq 'vulkan') { 'auto' } else { 'none' }
    $mode = if ($CheckOnly) { 'doctor' } else { 'serve' }
    $options = @($mode, '--model', $model, '--ctx', '4096', '--gpu', $gpu, '--cap', '128')
    if ($CheckOnly) {
        $options += @('--deep', '--json')
    } else {
        $options += @('--host', '127.0.0.1', '--port', "$Port", '--model-id', 'qwen36-35b-a3b-colibri-i4-gs64', '--no-think')
    }
    & $python $launcher @options
    $exitCode = $LASTEXITCODE
} finally {
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
}
exit $exitCode
