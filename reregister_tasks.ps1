# reregister_tasks.ps1
#
# One-time fix for the recurring "ghost / unkillable worker" problem.
#
# Run this ONCE from an ELEVATED (Administrator) PowerShell. It re-registers the
# 0DTE-Worker and 0DTE-Dashboard scheduled tasks so that:
#
#   1. RunLevel = LeastPrivilege (Limited) -> the processes (and any orphaned
#                                   children) run NON-elevated, so a normal
#                                   `Stop-Process` / `Stop-ScheduledTask` can kill
#                                   them. No more "Access is denied" on a leftover
#                                   worker. (The Dashboard was HighestAvailable =
#                                   elevated; that was the source of the unkillable
#                                   orphans.)
#
#   2. MultipleInstancesPolicy = StopExisting -> restarting the task EVICTS the
#                                   running instance first, instead of the old
#                                   `IgnoreNew` policy that silently refused to
#                                   start a new one while a ghost was still alive.
#
# Combined with the single-instance socket lock already in worker.py, this closes
# the loop: you can always restart cleanly, and nothing double-runs.
#
# NOTE: these tasks are registered from XML rather than the *-ScheduledTask*
# cmdlets, because New-ScheduledTaskSettingsSet cannot express StopExisting
# (its -MultipleInstances enum only offers Parallel/Queue/IgnoreNew). The XML
# schema does support it.
#
# Triggers (at boot + at logon), the actions, and the S4U logon type are kept
# exactly as they are today - only the run level and instance policy change.

$ErrorActionPreference = 'Stop'

# --- must be elevated -------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent() `
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "This script must be run from an ELEVATED (Administrator) PowerShell. Right-click PowerShell -> Run as administrator, then run it again."
    exit 1
}

$root   = 'C:\Users\slavk\OneDrive\Documents\Projects\0DTE'
$python = 'C:\Users\slavk\.pyenv\pyenv-win\versions\3.13.2\python.exe'
$userId = "$env:USERDOMAIN\$env:USERNAME"

Write-Host "Re-registering 0DTE scheduled tasks as user '$userId' (Limited level, StopExisting policy)..." -ForegroundColor Cyan

# --- 1. stop + remove any existing tasks (kills their tracked instances) ------
foreach ($name in '0DTE-Worker', '0DTE-Dashboard') {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Write-Host "  stopping + unregistering $name ..."
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
    }
}

# --- 2. sweep up any leftover orphans (now killable - we are elevated) -------
Write-Host "  sweeping leftover python worker/dashboard processes ..."
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match 'worker\.py' -or $_.CommandLine -match 'streamlit' } |
    ForEach-Object {
        Write-Host ("    killing pid {0}: {1}" -f $_.ProcessId, $_.CommandLine)
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

# --- 3. build task XML (StopExisting + LeastPrivilege) ------------------------
function New-TaskXml {
    param(
        [string]$Description,
        [string]$Command,
        [string]$Arguments,   # may be empty
        [string]$WorkingDir,
        [string]$User
    )
    $argLine = if ([string]::IsNullOrEmpty($Arguments)) { '' } else { "      <Arguments>$Arguments</Arguments>`r`n" }
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$Description</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$User</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <MultipleInstancesPolicy>StopExisting</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Count>5</Count>
      <Interval>PT1M</Interval>
    </RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <BootTrigger />
    <LogonTrigger />
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>$Command</Command>
$argLine      <WorkingDirectory>$WorkingDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

# --- 4. worker ---------------------------------------------------------------
# Launch python.exe DIRECTLY (not via run_worker.bat): with no cmd.exe parent,
# Task Scheduler tracks the python process itself and terminates it on stop, so
# a stopped task cannot leave an orphaned worker holding the lock. worker.py now
# does its own log rotation + file logging (see _setup_file_logging).
$workerXml = New-TaskXml -Description '0DTE background worker (polls signals)' `
    -Command $python -Arguments '-u worker.py' -WorkingDir $root -User $userId
Register-ScheduledTask -TaskName '0DTE-Worker' -Xml $workerXml -User $userId -Force | Out-Null
Write-Host "  registered 0DTE-Worker" -ForegroundColor Green

# --- 5. dashboard ------------------------------------------------------------
$dashXml = New-TaskXml -Description '0DTE paper trading Streamlit dashboard' `
    -Command $python `
    -Arguments '-m streamlit run dashboard.py --server.address 0.0.0.0 --server.port 8501' `
    -WorkingDir $root -User $userId
Register-ScheduledTask -TaskName '0DTE-Dashboard' -Xml $dashXml -User $userId -Force | Out-Null
Write-Host "  registered 0DTE-Dashboard" -ForegroundColor Green

# --- 6. start + verify -------------------------------------------------------
Write-Host "Starting both tasks ..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName '0DTE-Worker'
Start-ScheduledTask -TaskName '0DTE-Dashboard'
Start-Sleep -Seconds 4

Write-Host "`nTask state:" -ForegroundColor Cyan
Get-ScheduledTask -TaskName '0DTE-Worker', '0DTE-Dashboard' |
    Select-Object TaskName,
        @{n = 'State';    e = { $_.State }},
        @{n = 'RunLevel'; e = { $_.Principal.RunLevel }},
        @{n = 'Instances';e = { $_.Settings.MultipleInstances }} |
    Format-Table -AutoSize

Write-Host "Python processes now running:" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match 'worker\.py' -or $_.CommandLine -match 'streamlit' } |
    Select-Object ProcessId, CommandLine | Format-Table -AutoSize -Wrap

Write-Host "`nDone. Expect exactly ONE worker.py and ONE streamlit process above." -ForegroundColor Green
Write-Host "The worker will create the worker_heartbeat table on its first cycle;" -ForegroundColor Green
Write-Host "the dashboard's 'Worker alive' indicator should go green within a minute." -ForegroundColor Green
