# socket_guard.ps1
# Sample local TCP socket state every 10 minutes and append one line to the log.
# When the number of Bound (bound but not connected) sockets exceeds the threshold,
# the machine is considered to be running out of ephemeral ports, so explorer.exe is
# restarted to release the leaked sockets (self-healing without rebooting the server).
#
# Background: explorer.exe once leaked 16,374 Bound sockets, exhausting the Windows
# dynamic port range (16384 ports starting at 49152). The application then failed to
# create sockets (WinError 10055) and could not even reach the local MySQL server.
#
# NOTE: keep this file pure ASCII. Windows PowerShell 5.1 reads .ps1 files as ANSI
# when no BOM is present, so non-ASCII text gets mangled and may break parsing.

$ErrorActionPreference = 'Stop'

$LogPath   = 'C:\luosen\effic\logs\socket_guard.log'
$Threshold = 8000          # half of the 16384-port dynamic range
$MaxLogMB  = 5

function Write-GuardLog {
    param([string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    try {
        Add-Content -Path $LogPath -Value "$stamp $Message" -Encoding utf8 -ErrorAction Stop
    } catch {
        # never let a logging failure break the self-healing logic
    }
}

try {
    if ((Test-Path $LogPath) -and (Get-Item $LogPath).Length -gt ($MaxLogMB * 1MB)) {
        Move-Item -Path $LogPath -Destination "$LogPath.1" -Force
    }

    $conns       = Get-NetTCPConnection -ErrorAction Stop
    $bound       = @($conns | Where-Object { $_.State -eq 'Bound' }).Count
    $established = @($conns | Where-Object { $_.State -eq 'Established' }).Count
    $timeWait    = @($conns | Where-Object { $_.State -eq 'TimeWait' }).Count

    $os = Get-CimInstance Win32_OperatingSystem
    $commitUsedMB  = [math]::Round(($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1KB, 0)
    $commitTotalMB = [math]::Round($os.TotalVirtualMemorySize / 1KB, 0)

    Write-GuardLog ("Bound={0} Established={1} TimeWait={2} Commit={3}/{4}MB" -f `
        $bound, $established, $timeWait, $commitUsedMB, $commitTotalMB)

    if ($bound -gt $Threshold) {
        # find the processes holding most Bound sockets, to locate the real leak source
        $top = $conns | Where-Object { $_.State -eq 'Bound' } |
               Group-Object OwningProcess |
               Sort-Object Count -Descending |
               Select-Object -First 3 |
               ForEach-Object {
                   $proc = Get-Process -Id $_.Name -ErrorAction SilentlyContinue
                   $pname = if ($proc) { $proc.ProcessName } else { 'unknown' }
                   "$pname(pid=$($_.Name))=$($_.Count)"
               }
        Write-GuardLog ("ALERT Bound={0} exceeds threshold {1}, top holders: {2}" -f $bound, $Threshold, ($top -join ', '))

        $explorers = @(Get-Process explorer -ErrorAction SilentlyContinue)
        if ($explorers.Count -gt 0) {
            $explorers | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 3
            $after = @(Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $_.State -eq 'Bound' }).Count
            Write-GuardLog "explorer restarted, Bound dropped to $after"
        } else {
            Write-GuardLog "explorer not running, skip self-healing, check other processes for socket leaks"
        }
    }
} catch {
    Write-GuardLog ("ERROR " + $_.Exception.Message)
    exit 1
}
