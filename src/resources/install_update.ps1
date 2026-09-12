param(
    [Parameter(Mandatory = $true)][string]$StagePath,
    [Parameter(Mandatory = $true)][string]$InstallPath,
    [int]$ParentProcessId = 0,
    [switch]$NoRestart
)
$ErrorActionPreference = 'Stop'
$stageRoot = (Resolve-Path -LiteralPath $StagePath).Path
$installRoot = (Resolve-Path -LiteralPath $InstallPath).Path
$backupRoot = Join-Path $stageRoot 'backup'
$installed = @()
$backedUp = @()

function Get-ChildFile([string]$Root, [string]$Name) {
    $path = [IO.Path]::GetFullPath((Join-Path $Root $Name))
    $prefix = $Root.TrimEnd('\') + '\'
    if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Update target is outside its expected directory'
    }
    return $path
}

try {
    if ($stageRoot -eq $installRoot) { throw 'Update staging and install paths must differ' }
    if (-not (Test-Path -LiteralPath (Get-ChildFile $stageRoot 'DG-LAB-VRCOSC.exe') -PathType Leaf)) {
        throw 'The update executable is missing'
    }
    if ($ParentProcessId -gt 0) {
        $parentProcess = Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue
        if ($parentProcess -and -not $parentProcess.WaitForExit(60000)) {
            throw 'The application did not exit within 60 seconds'
        }
    }
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    # Fixed application-owned filenames only. User configuration is never replaced.
    foreach ($name in @('DG-LAB-VRCOSC.exe', 'build-info.json', 'build-info.txt')) {
        $source = Get-ChildFile $stageRoot $name
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }
        $target = Get-ChildFile $installRoot $name
        $backup = Get-ChildFile $backupRoot $name
        if (Test-Path -LiteralPath $target) {
            Move-Item -LiteralPath $target -Destination $backup
            # A cross-volume Windows move can report success after copying a
            # locked file while leaving its source intact. It is not a completed
            # backup move and must not be "restored" over the unchanged original.
            if (Test-Path -LiteralPath $target) {
                throw "The installed file is still in use and could not be moved: $target"
            }
            $backedUp += $name
        }
        Move-Item -LiteralPath $source -Destination $target
        $installed += $name
    }
    if (-not $NoRestart) {
        Start-Process -FilePath (Get-ChildFile $installRoot 'DG-LAB-VRCOSC.exe') -WorkingDirectory $installRoot
    }
    # Only remove the private temp directory created by this updater. Never
    # recursively remove an arbitrary path supplied on the command line.
    $workRoot = [IO.Path]::GetFullPath((Split-Path -Parent $stageRoot))
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ((Split-Path -Leaf $stageRoot) -eq 'stage' -and
        (Split-Path -Leaf $workRoot) -like 'dglab-update-*' -and
        (Split-Path -Parent $workRoot).TrimEnd('\') -eq $tempRoot -and
        -not $installRoot.StartsWith($workRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $workRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 0
} catch {
    $failure = $_.Exception.Message
    $restoreErrors = @()
    # Restore files if a replace or restart failed. All paths are checked above.
    foreach ($name in $installed) {
        $target = Get-ChildFile $installRoot $name
        try {
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }
        } catch { $restoreErrors += $_.Exception.Message }
    }
    foreach ($name in $backedUp) {
        try {
            Move-Item -LiteralPath (Get-ChildFile $backupRoot $name) -Destination (Get-ChildFile $installRoot $name) -Force
        } catch { $restoreErrors += $_.Exception.Message }
    }
    if ($restoreErrors.Count -gt 0) {
        $failure += "`nRecovery incomplete. Backup: $backupRoot`n" + ($restoreErrors -join "`n")
    } else { $failure += "`nPrevious application files were restored." }
    $failure | Set-Content -LiteralPath (Join-Path $stageRoot 'update-error.txt') -Encoding UTF8
    if (-not $NoRestart) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show("Update failed.`n$failure", 'DG-LAB-VRCOSC') | Out-Null
    }
    exit 1
}
