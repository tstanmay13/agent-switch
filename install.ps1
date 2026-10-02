$ErrorActionPreference = 'Stop'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 "$PSScriptRoot/install.py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python "$PSScriptRoot/install.py"
} else {
    throw 'Install Python 3, then run this installer again.'
}
if ($LASTEXITCODE -ne 0) { throw 'agent-switch installation failed.' }
$agentSwitchBin = Join-Path $HOME '.local\bin'
$userPath = [string][Environment]::GetEnvironmentVariable('Path', 'User')
if ($agentSwitchBin -notin ($userPath -split ';')) {
    [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $agentSwitchBin).TrimStart(';')), 'User')
}
$env:Path = "$agentSwitchBin;$env:Path"
Write-Host 'PATH updated. Restart agent sessions to discover the installed skills.'
