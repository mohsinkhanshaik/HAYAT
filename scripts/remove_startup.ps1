[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$TaskName = 'PersonalAI-VoiceAssistant'
)

$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ShouldProcess($TaskName, 'Remove the AI voice assistant startup task')) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed $TaskName."
}
