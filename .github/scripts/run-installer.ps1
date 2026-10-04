# Runs an Inno Setup installer silently with a timeout. It always prints the installer log, the il2ks logs and the process tree
# afterwards; on a timeout it also kills the installer, so a hang shows where it hangs instead of eating the job's time limit.
# Usage: run-installer.ps1 -Exe <path> -LogFile <setup log> -Arguments <installer switches...> [-TimeoutSeconds 900]
param(
  [Parameter(Mandatory)] [string] $Exe,
  [Parameter(Mandatory)] [string] $LogFile,
  [Parameter(Mandatory)] [string[]] $Arguments,
  [int] $TimeoutSeconds = 900
)
$ErrorActionPreference = 'Continue'

function Show-Diagnostics {
  Write-Host "::group::installer log ($LogFile)"
  if (Test-Path $LogFile) { Get-Content $LogFile -Tail 150 } else { Write-Host "(no log file)" }
  Write-Host "::endgroup::"
  Write-Host "::group::il2ks logs in ProgramData"
  Get-ChildItem "$env:ProgramData\il2ks" -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like '*\logs\*' } |
    ForEach-Object { "== $($_.FullName)"; Get-Content $_.FullName -Tail 30 }
  Write-Host "::endgroup::"
  Write-Host "::group::process tree"
  Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match 'il2ks|python|cmd|icacls|sc\.exe|netsh|caddy' } |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine | Format-List | Out-String -Width 250
  Write-Host "::endgroup::"
}

$arglist = ($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
# No -Wait: that also waits for every process the installer started, and has no timeout.
$p = Start-Process -FilePath $Exe -ArgumentList $arglist -PassThru
if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
  Write-Host "::error::the installer did not finish within $TimeoutSeconds seconds"
  Show-Diagnostics
  Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'il2ks-setup*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  throw "installer timed out"
}
$p.WaitForExit()
Show-Diagnostics
if ($p.ExitCode -ne 0) { throw "installer exit code $($p.ExitCode)" }
