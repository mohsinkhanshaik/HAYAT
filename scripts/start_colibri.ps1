# Run the optional Colibri engine on loopback, separate from HAYAT's default.
# -Profile original   : the original-Qwen int4 container (different weights from hayat:latest).
# -Profile foundation : HAYAT's own brain GGUF read in place (scripts/setup_colibri_foundation.py; Colibri 2.1.0+).
# The command, environment and RAM thresholds come from app/models/colibri_service.py, shared with the GUI and
# scripts/benchmark_colibri.py.
param(
    [ValidateSet('cpu', 'vulkan', 'cuda')][string]$Backend = 'cpu',
    [ValidateSet('original', 'foundation')][string]$Profile = 'original',
    [ValidateRange(1024, 65535)][int]$Port = 8000,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Missing local installation file: $python" }
$plan = & $python -c @"
import json, sys
sys.path.insert(0, r'$workspace')
from app.models.colibri_service import ColibriService, PROFILES
service = ColibriService()
command, env = service.command('$Profile', '$Backend', $Port)
spec = PROFILES['$Profile']
marker = service.root / spec['model_dir'] / spec['marker']
print(json.dumps({'command': command, 'env': {k: v for k, v in env.items() if k.startswith(('COLI_', 'OMP_', 'MKL_'))},
                  'model': str(service.root / spec['model_dir']), 'marker': str(marker), 'ready': marker.is_file()}))
"@ | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the Colibri launch plan.' }
if (-not $plan.ready) { throw "Missing local installation file: $($plan.marker)" }
$launcher = $plan.command[1]
if ($CheckOnly) {
    & $python $launcher doctor --model $plan.model --ctx 4096 --json
    exit $LASTEXITCODE
}
& $python -c @"
import sys
sys.path.insert(0, r'$workspace')
from app.models.colibri_service import ColibriService
try:
    ColibriService().preflight('$Backend')
except RuntimeError as error:
    print(error, file=sys.stderr)
    raise SystemExit(1)
"@
if ($LASTEXITCODE -ne 0) { exit 1 }
$previous = @{}
foreach ($property in $plan.env.PSObject.Properties) {
    $previous[$property.Name] = [Environment]::GetEnvironmentVariable($property.Name, 'Process')
}
$exitCode = 1
try {
    foreach ($property in $plan.env.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($property.Name, $property.Value, 'Process')
    }
    $arguments = @($plan.command | Select-Object -Skip 1)
    & $python @arguments
    $exitCode = $LASTEXITCODE
} finally {
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
}
exit $exitCode
