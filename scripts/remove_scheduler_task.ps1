[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$TaskName = 'PersonalAI-AgentScheduler'
)

$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ShouldProcess($TaskName, 'Remove the personal AI scheduler startup task')) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed $TaskName."
}
