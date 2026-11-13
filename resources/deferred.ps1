param([Parameter(Mandatory=$true)][string]$Receipt)
$ErrorActionPreference = 'Stop'
function Save-Record($value) {
    $json = $value | ConvertTo-Json -Depth 10
    foreach ($destination in @($value.sidecar, $Receipt)) {
        $temporary = $destination + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
        [IO.File]::WriteAllText($temporary, $json, (New-Object Text.UTF8Encoding($false)))
        if ([IO.File]::Exists($destination)) {
            $previous = $temporary + '.previous'
            [IO.File]::Replace($temporary, $destination, $previous)
            Remove-Item -LiteralPath $previous
        }
        else { [IO.File]::Move($temporary, $destination) }
    }
}
function Hash-File($path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Remove-Staged {
    if ([IO.File]::Exists($pending) -and (Hash-File $pending) -eq $record.replacementSha) {
        Remove-Item -LiteralPath $pending
    }
}
function Mod-Active($configuration) {
    if (-not $configuration.enabled -or -not $configuration.modPath -or -not [IO.File]::Exists($configuration.modPath)) { return $false }
    if ($configuration.modName -and $configuration.userDir) {
        $dbPath = Join-Path $configuration.userDir 'mods/db.json'
        if (Test-Path -LiteralPath $dbPath) {
            $db = Get-Content -LiteralPath $dbPath -Raw | ConvertFrom-Json
            $entry = $db.mods.($configuration.modName)
            if ($entry -and $entry.active -eq $false) { return $false }
        }
    }
    return $true
}
function Show-ReadyNotice {
    if ($record.notify) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            'The railway add-on is ready. Close BeamNG.Earth completely, wait a few seconds, then start it using its normal launcher. No other launcher is needed.',
            'Earth Railway Filter — restart required', 'OK', 'Information') | Out-Null
    }
}
try {
    $record = Get-Content -LiteralPath $Receipt -Raw | ConvertFrom-Json
    $target = [IO.Path]::GetFullPath($record.target)
    $pending = [IO.Path]::GetFullPath($record.pending)
    if ([IO.Path]::GetDirectoryName($target) -ne [IO.Path]::GetDirectoryName($pending) -or
        -not [IO.Path]::GetFileName($pending).StartsWith([IO.Path]::GetFileName($target) + '.earthRailFilter.') -or
        -not $pending.EndsWith('.pending')) { throw 'Invalid staged-file path.' }
    if ($record.companionPid) {
        # The companion must exit normally before the user restarts Earth.
        # Its built-in parent watcher can then no longer terminate this helper.
        $companionEnd = (Get-Date).AddSeconds(30)
        while ($null -ne (Get-Process -Id $record.companionPid -ErrorAction SilentlyContinue)) {
            if ((Get-Date) -gt $companionEnd) { throw 'Setup did not finish closing. Start this add-on again from the Add-ons menu.' }
            Start-Sleep -Milliseconds 200
        }
    }
    Show-ReadyNotice
    $deadline = (Get-Date).AddHours(12)
    $ready = $false
    do {
        $latest = Get-Content -LiteralPath $Receipt -Raw | ConvertFrom-Json
        $configuration = Get-Content -LiteralPath $record.config -Raw | ConvertFrom-Json
        # Removal, re-enabling or a newer installation request cancels this job.
        if ($latest.token -ne $record.token -or $configuration.requestToken -ne $record.token) { Remove-Staged; return }
        if ($target.EndsWith('.exe', [StringComparison]::OrdinalIgnoreCase)) {
            $busy = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'BeamNG.Earth%.exe'" |
                Where-Object { $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -eq $target }).Count -gt 0
        } else {
            $busy = $record.serverPid -gt 0 -and $null -ne (Get-Process -Id $record.serverPid -ErrorAction SilentlyContinue)
        }
        if (-not $busy) { $ready = $true; break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if (-not $ready) { throw 'Installation timed out waiting for Earth to close. Use Apply server patch again.' }
    if ($record.operation -eq 'restore' -and $record.onlyIfInactive -and (Mod-Active $configuration)) {
        Remove-Staged
        $record.state = 'installed'; $record.message = 'Local server patch installed.'
        Save-Record $record
        return
    }
    if ($record.operation -eq 'install' -and -not (Mod-Active $configuration)) {
        Remove-Staged
        $record.state = 'cancelled'; $record.message = 'The add-on was disabled or removed; server patch cancelled.'
        Save-Record $record
        return
    }
    if ((Hash-File $target) -ne $record.expectedSha) { throw 'Server changed after preparation; an update was preserved.' }
    if ((Hash-File $pending) -ne $record.replacementSha) { throw 'Staged file changed; original server was preserved.' }
    # File.Replace is atomic and the files are on the same volume. Retry only
    # while Windows finishes releasing executable handles; never kill an app.
    $replaced = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        $latest = Get-Content -LiteralPath $Receipt -Raw | ConvertFrom-Json
        $configuration = Get-Content -LiteralPath $record.config -Raw | ConvertFrom-Json
        if ($latest.token -ne $record.token -or $configuration.requestToken -ne $record.token) { Remove-Staged; return }
        if ((Hash-File $target) -ne $record.expectedSha) { throw 'Server changed before replacement; original file preserved.' }
        try {
            $swapBackup = $target + '.earthRailFilter.swap.' + [guid]::NewGuid().ToString('N')
            [IO.File]::Replace($pending, $target, $swapBackup)
            $replaced = $true
            break
        }
        catch [IO.IOException] { Start-Sleep -Seconds 2 }
    }
    if (-not $replaced) { throw 'Windows kept the server file locked. Close Earth and retry the patch.' }
    if ((Hash-File $target) -ne $record.replacementSha) { throw 'Replacement verification failed. The original backup is available.' }
    Remove-Item -LiteralPath $swapBackup
    if ($record.operation -eq 'restore') {
        $record.state = 'restored'; $record.message = 'Original server restored. Start BeamNG.Earth normally.'
    } else {
        $record.state = 'installed'; $record.message = 'Local patch installed. Start BeamNG.Earth normally.'
    }
    Save-Record $record
} catch {
    if ($record) {
        $record.state = 'error'; $record.message = $_.Exception.Message
        Save-Record $record
    }
}
