# Install livekit-agent-simulator from GitHub Releases. Zero prereqs:
# bootstraps uv if missing, prefers CI-built wheel, else git/source archive.
#
#   irm "https://raw.githubusercontent.com/quangdang46/livekit-agent-simulator/main/install.ps1" | iex
#
#   .\install.ps1 -GitRef v0.1.0 -Verify
#   .\install.ps1 -Uninstall
#
#Requires -Version 5.1
[CmdletBinding()]
param(
    [Alias("Version")]
    # Empty → latest GitHub Release (CI wheel). Use -GitRef main for tip of main.
    [string]$GitRef = $(if ($env:LK_SIM_REF) { $env:LK_SIM_REF } else { "" }),
    [switch]$NoMcp,
    [switch]$Verify,
    [switch]$Uninstall,
    [switch]$Quiet,
    # Force git/source path even when a release wheel exists.
    [switch]$FromGit
)

$ErrorActionPreference = "Stop"
$BinaryName = "lk-sim"
$McpServerName = "livekit-agent-simulator"
$PkgName = "livekit-agent-simulator"
$Owner = "quangdang46"
$Repo = "livekit-agent-simulator"
$UvInstallUrl = "https://astral.sh/uv/install.ps1"

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    if ($Quiet -and $Level -eq "INFO") { return }
    $prefix = "[$BinaryName]"
    if ($Level -eq "WARN") { Write-Host "$prefix WARN: $Message" -ForegroundColor Yellow }
    elseif ($Level -eq "ERROR") { Write-Host "$prefix ERROR: $Message" -ForegroundColor Red }
    else { Write-Host "$prefix $Message" }
}

function Ensure-DirOnPath {
    param([string]$Dir)
    if (-not $Dir) { return }
    if (-not (Test-Path $Dir)) {
        New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    }
    # Current session
    $parts = $env:PATH -split ';' | Where-Object { $_ -and ($_ -ne $Dir) }
    $env:PATH = (@($Dir) + $parts) -join ';'
    # Persist for new shells (user PATH)
    try {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not $userPath) { $userPath = "" }
        if (($userPath -split ';') -notcontains $Dir) {
            $newUser = if ($userPath.Trim()) { "$Dir;$userPath" } else { $Dir }
            [Environment]::SetEnvironmentVariable("Path", $newUser, "User")
            Write-Log "PATH += $Dir (user)"
        }
    } catch {
        Write-Log "Could not persist PATH entry for $Dir : $_" "WARN"
    }
}

function Refresh-CommandPath {
    # Drop cached command lookups so newly installed bins are found.
    $names = @("uv", "pipx", $BinaryName, "lk-sim-mcp")
    foreach ($n in $names) {
        if (Get-Command $n -ErrorAction SilentlyContinue) {
            # no-op: Get-Command with full re-resolve below after PATH change
        }
    }
    $env:PATH = $env:PATH
}

function Get-UvCandidates {
    @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\uv"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "uv\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\uv\uv.exe")
    )
}

function Resolve-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in (Get-UvCandidates)) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Get-WindowsUvZipName {
    # Match astral-sh/uv release asset names.
    $arch = $env:PROCESSOR_ARCHITECTURE
    switch -Regex ($arch) {
        '^(ARM64|arm64)$' { return "uv-aarch64-pc-windows-msvc.zip" }
        '^(AMD64|x86_64|x64)$' { return "uv-x86_64-pc-windows-msvc.zip" }
        '^(x86|X86)$' { return "uv-i686-pc-windows-msvc.zip" }
        default {
            if ([Environment]::Is64BitOperatingSystem) { return "uv-x86_64-pc-windows-msvc.zip" }
            return "uv-i686-pc-windows-msvc.zip"
        }
    }
}

function Install-UvBinaryDirect {
    # No PowerShell execution-policy dependency: download official uv zip and place uv.exe.
    $destDir = Join-Path $env:USERPROFILE ".local\bin"
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    $zipName = Get-WindowsUvZipName
    $url = "https://github.com/astral-sh/uv/releases/latest/download/$zipName"
    $work = Join-Path $env:TEMP ("uv-bootstrap-" + [guid]::NewGuid().ToString("n"))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $zip = Join-Path $work $zipName
    Write-Log "Downloading uv binary: $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $work -Force
        $exe = Get-ChildItem -Path $work -Recurse -Filter "uv.exe" | Select-Object -First 1
        if (-not $exe) { throw "uv.exe not found in $zipName" }
        $target = Join-Path $destDir "uv.exe"
        Copy-Item -Path $exe.FullName -Destination $target -Force
        # Companion binaries if present (optional)
        Get-ChildItem -Path $work -Recurse -Filter "uvx.exe" -ErrorAction SilentlyContinue |
            ForEach-Object { Copy-Item $_.FullName (Join-Path $destDir $_.Name) -Force }
        Write-Log "Installed uv.exe → $target"
        return $target
    } finally {
        try { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue } catch {}
    }
}

function Install-UvViaOfficialScript {
    # Astral docs: must use Bypass/RemoteSigned. Nested process so machine policy
    # Restricted does not block the official install.ps1.
    # https://docs.astral.sh/uv/getting-started/installation/
    Write-Log "Running official uv installer with -ExecutionPolicy Bypass"
    $cmd = "irm '$UvInstallUrl' | iex"
    $psExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path $psExe)) { $psExe = "powershell.exe" }

    $p = Start-Process -FilePath $psExe -ArgumentList @(
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-Command", $cmd
    ) -Wait -PassThru -NoNewWindow
    return ($p.ExitCode -eq 0)
}

function Ensure-Uv {
    $uv = Resolve-Uv
    if ($uv) {
        Write-Log "Using uv: $uv"
        return $uv
    }

    Write-Log "uv not found — bootstrapping (no manual install needed)"

    # 1) Official installer under Bypass (handles PATH + shell integration).
    try {
        [void](Install-UvViaOfficialScript)
    } catch {
        Write-Log "Official uv installer failed: $_" "WARN"
    }

    Ensure-DirOnPath (Join-Path $env:USERPROFILE ".local\bin")
    Ensure-DirOnPath (Join-Path $env:USERPROFILE ".cargo\bin")
    Refresh-CommandPath
    $uv = Resolve-Uv
    if ($uv) {
        Write-Log "Bootstrapped uv: $uv"
        return $uv
    }

    # 2) Direct binary download — ignores execution policy entirely.
    Write-Log "Falling back to direct uv.exe download from GitHub Releases"
    try {
        $uv = Install-UvBinaryDirect
    } catch {
        throw "uv bootstrap failed (script + binary): $_  Install manually: https://docs.astral.sh/uv/getting-started/installation/"
    }

    Ensure-DirOnPath (Join-Path $env:USERPROFILE ".local\bin")
    Refresh-CommandPath
    $uv = Resolve-Uv
    if (-not $uv) {
        throw "uv installed but not found on PATH. Open a new PowerShell and re-run, or add %USERPROFILE%\.local\bin to PATH."
    }
    Write-Log "Bootstrapped uv: $uv"
    return $uv
}

function Merge-JsonIntoFile {
    param(
        [string]$FilePath,
        [string]$Key,
        [hashtable]$Value
    )
    $dir = Split-Path -Parent $FilePath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $data = [ordered]@{}
    if (Test-Path $FilePath) {
        try {
            $raw = Get-Content -Path $FilePath -Raw -ErrorAction Stop
            if ($raw.Trim()) {
                $obj = $raw | ConvertFrom-Json
                $data = [ordered]@{}
                foreach ($p in $obj.PSObject.Properties) {
                    $data[$p.Name] = $p.Value
                }
            }
        } catch {
            $data = [ordered]@{}
        }
    }

    if (-not $data.Contains($Key)) {
        $data[$Key] = [ordered]@{}
    }

    $bucket = $data[$Key]
    $bucketMap = [ordered]@{}
    if ($bucket -is [System.Collections.IDictionary]) {
        foreach ($k in $bucket.Keys) { $bucketMap[$k] = $bucket[$k] }
    } elseif ($null -ne $bucket -and $bucket.PSObject) {
        foreach ($p in $bucket.PSObject.Properties) { $bucketMap[$p.Name] = $p.Value }
    }

    foreach ($k in $Value.Keys) {
        $bucketMap[$k] = $Value[$k]
    }
    $data[$Key] = $bucketMap

    ($data | ConvertTo-Json -Depth 12) + "`n" | Set-Content -Path $FilePath -Encoding UTF8
}

function Remove-McpFromFile {
    param([string]$FilePath, [string]$ParentKey = "mcpServers", [string]$ServerName)
    if (-not (Test-Path $FilePath)) { return }
    try {
        $obj = Get-Content -Path $FilePath -Raw | ConvertFrom-Json
        if ($null -eq $obj.$ParentKey) { return }
        $map = [ordered]@{}
        foreach ($p in $obj.PSObject.Properties) {
            if ($p.Name -eq $ParentKey) {
                $inner = [ordered]@{}
                foreach ($ip in $p.Value.PSObject.Properties) {
                    if ($ip.Name -ne $ServerName) { $inner[$ip.Name] = $ip.Value }
                }
                $map[$p.Name] = $inner
            } else {
                $map[$p.Name] = $p.Value
            }
        }
        ($map | ConvertTo-Json -Depth 12) + "`n" | Set-Content -Path $FilePath -Encoding UTF8
    } catch {
        Write-Log "Could not edit $FilePath : $_" "WARN"
    }
}

function Resolve-LkSim {
    $cmd = Get-Command $BinaryName -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\$BinaryName.exe"),
        (Join-Path $env:USERPROFILE ".local\bin\$BinaryName"),
        (Join-Path $env:LOCALAPPDATA "uv\tools\$PkgName\bin\$BinaryName.exe"),
        (Join-Path $env:USERPROFILE ".local\share\uv\tools\$PkgName\bin\$BinaryName.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Scripts\$BinaryName.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Configure-AllMcpProviders {
    $binary = Resolve-LkSim
    if (-not $binary) {
        Write-Log "lk-sim not found on PATH — skip MCP provider config" "WARN"
        return
    }
    Write-Log "Configuring MCP providers → $binary mcp"
    $entry = @{
        $McpServerName = @{
            command = $binary
            args    = @("mcp")
            env     = @{}
        }
    }

    Merge-JsonIntoFile -FilePath (Join-Path $env:USERPROFILE ".claude.json") -Key "mcpServers" -Value $entry
    Merge-JsonIntoFile -FilePath (Join-Path $env:USERPROFILE ".cursor\mcp.json") -Key "mcpServers" -Value $entry

    $cline = Join-Path $env:APPDATA "Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json"
    if (Test-Path (Split-Path $cline)) {
        Merge-JsonIntoFile -FilePath $cline -Key "mcpServers" -Value $entry
    }

    Merge-JsonIntoFile -FilePath (Join-Path $env:USERPROFILE ".codeium\windsurf\mcp_config.json") -Key "mcpServers" -Value $entry
    Merge-JsonIntoFile -FilePath (Join-Path $env:USERPROFILE ".vscode\mcp.json") -Key "servers" -Value $entry
    Merge-JsonIntoFile -FilePath (Join-Path $env:USERPROFILE ".gemini\settings.json") -Key "mcpServers" -Value $entry
    Merge-JsonIntoFile -FilePath (Join-Path $env:USERPROFILE ".aws\amazonq\mcp.json") -Key "mcpServers" -Value $entry
    Merge-JsonIntoFile -FilePath (Join-Path $env:USERPROFILE ".aws\amazonq\default.json") -Key "mcpServers" -Value $entry

    $opencode = Join-Path $env:USERPROFILE ".opencode.json"
    if ((Test-Path $opencode) -or (Test-Path (Join-Path $env:USERPROFILE ".config\opencode"))) {
        $ocEntry = @{
            $McpServerName = @{
                type    = "stdio"
                command = $binary
                args    = @("mcp")
                env     = @()
            }
        }
        Merge-JsonIntoFile -FilePath $opencode -Key "mcpServers" -Value $ocEntry
    }

    $codexDir = Join-Path $env:USERPROFILE ".codex"
    $codex = Join-Path $codexDir "config.toml"
    if (Test-Path $codexDir) {
        if (-not (Test-Path $codex)) { New-Item -ItemType File -Path $codex -Force | Out-Null }
        $content = Get-Content -Path $codex -Raw -ErrorAction SilentlyContinue
        if ($content -notmatch "\[mcp_servers\.$([regex]::Escape($McpServerName))\]") {
            Add-Content -Path $codex -Value @"

[mcp_servers.$McpServerName]
type = "stdio"
command = "$binary"
args = ["mcp"]
"@
        }
    }
}

function Uninstall-All {
    Write-Log "Uninstalling $PkgName..."
    $uv = Resolve-Uv
    if ($uv) {
        try { & $uv tool uninstall $PkgName 2>$null } catch {}
    }
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        try { pipx uninstall $PkgName 2>$null } catch {}
    }
    Remove-McpFromFile -FilePath (Join-Path $env:USERPROFILE ".claude.json") -ServerName $McpServerName
    Remove-McpFromFile -FilePath (Join-Path $env:USERPROFILE ".cursor\mcp.json") -ServerName $McpServerName
    Remove-McpFromFile -FilePath (Join-Path $env:USERPROFILE ".vscode\mcp.json") -ParentKey "servers" -ServerName $McpServerName
    Remove-McpFromFile -FilePath (Join-Path $env:USERPROFILE ".gemini\settings.json") -ServerName $McpServerName
    Remove-McpFromFile -FilePath (Join-Path $env:USERPROFILE ".aws\amazonq\mcp.json") -ServerName $McpServerName
    Remove-McpFromFile -FilePath (Join-Path $env:USERPROFILE ".aws\amazonq\default.json") -ServerName $McpServerName
    Write-Log "Uninstalled $PkgName"
}

function Get-LatestReleaseTag {
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Owner/$Repo/releases/latest" -UseBasicParsing
        if ($rel.tag_name) { return [string]$rel.tag_name }
    } catch {
        Write-Log "Could not resolve latest release: $_" "WARN"
    }
    return $null
}

function Resolve-InstallRef {
    if ($GitRef -and $GitRef.Trim()) {
        return $GitRef.Trim()
    }
    $latest = Get-LatestReleaseTag
    if ($latest) {
        Write-Log "Default ref → latest release $latest (CI wheel)"
        return $latest
    }
    Write-Log "No GitHub releases found — using main (source install)" "WARN"
    return "main"
}

function Get-ReleaseTagFromRef {
    param([string]$Ref)
    if (-not $Ref) { return $null }
    if ($Ref -match '^[0-9]+\.[0-9]+') { return "v$Ref" }
    if ($Ref -match '^v[0-9]+\.[0-9]+') { return $Ref }
    return $null
}

function Install-FromReleaseWheel {
    param([string]$UvPath, [string]$Ref)

    if ($FromGit) { return $false }

    $tag = Get-ReleaseTagFromRef -Ref $Ref
    if (-not $tag) { return $false }

    Write-Log "Looking for CI wheel on release $tag ..."
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$Owner/$Repo/releases/tags/$tag" -UseBasicParsing
    } catch {
        Write-Log "No GitHub release for $tag — will use source" "WARN"
        return $false
    }

    $asset = @($rel.assets) | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1
    if (-not $asset) {
        Write-Log "Release $tag has no .whl asset — will use source" "WARN"
        return $false
    }

    $work = Join-Path $env:TEMP ("lk-sim-whl-" + [guid]::NewGuid().ToString("n"))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $whl = Join-Path $work $asset.name
    Write-Log "Downloading CI wheel: $($asset.browser_download_url)"
    try {
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $whl -UseBasicParsing
    } catch {
        Write-Log "Wheel download failed: $_" "WARN"
        try { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue } catch {}
        return $false
    }
    if (-not (Test-Path $whl) -or (Get-Item $whl).Length -le 0) {
        Write-Log "Wheel file empty/missing" "WARN"
        return $false
    }

    Write-Log "uv tool install --force $whl  (prebuilt by CI — no local package build)"
    & $UvPath tool install --force $whl
    $code = $LASTEXITCODE
    try { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue } catch {}
    if ($code -ne 0) {
        Write-Log "uv tool install (wheel) failed exit $code" "WARN"
        return $false
    }
    return $true
}

function Install-FromArchive {
    param([string]$UvPath, [string]$Ref)

    $work = Join-Path $env:TEMP ("lk-sim-install-" + [guid]::NewGuid().ToString("n"))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $zip = Join-Path $work "src.zip"
    $urls = @(
        "https://github.com/$Owner/$Repo/archive/refs/tags/$Ref.zip",
        "https://github.com/$Owner/$Repo/archive/refs/heads/$Ref.zip"
    )

    $downloaded = $false
    foreach ($url in $urls) {
        Write-Log "Downloading source: $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
            if ((Test-Path $zip) -and ((Get-Item $zip).Length -gt 0)) {
                $downloaded = $true
                break
            }
        } catch {
            Write-Log "Not available: $url ($($_.Exception.Message))" "WARN"
        }
    }
    if (-not $downloaded) {
        throw "Could not download source for ref '$Ref' (tried tags + branches)."
    }

    Write-Log "Extracting source archive..."
    Expand-Archive -Path $zip -DestinationPath $work -Force
    $src = Get-ChildItem -Path $work -Directory | Where-Object {
        $_.Name -like "$Repo-*" -or $_.Name -eq $Repo
    } | Select-Object -First 1
    if (-not $src) {
        throw "Source tree not found after extract under $work"
    }
    if (-not (Test-Path (Join-Path $src.FullName "pyproject.toml"))) {
        throw "pyproject.toml missing in $($src.FullName)"
    }

    Write-Log "uv tool install --force $($src.FullName)"
    & $UvPath tool install --force $src.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "uv tool install failed (exit $LASTEXITCODE)"
    }

    try { Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue } catch {}
}

function Install-FromGitSpec {
    param([string]$UvPath, [string]$Ref)
    $spec = "git+https://github.com/$Owner/$Repo.git@$Ref"
    Write-Log "Source: $spec"
    & $UvPath tool install --force $spec
    if ($LASTEXITCODE -ne 0) {
        throw "uv tool install (git) failed (exit $LASTEXITCODE)"
    }
}

function Install-Package {
    param([string]$Ref)

    $uv = Ensure-Uv
    Ensure-DirOnPath (Join-Path $env:USERPROFILE ".local\bin")

    # 1) Prefer CI-built wheel from GitHub Release (no local build)
    if (Install-FromReleaseWheel -UvPath $uv -Ref $Ref) {
        return
    }

    # 2) git if available
    $hasGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
    if ($hasGit) {
        try {
            Install-FromGitSpec -UvPath $uv -Ref $Ref
            return
        } catch {
            Write-Log "git-based install failed; falling back to source archive: $_" "WARN"
        }
    } else {
        Write-Log "git not on PATH — installing from GitHub source archive (no Git required)"
    }

    # 3) source zip
    Install-FromArchive -UvPath $uv -Ref $Ref
}

if ($Uninstall) {
    Uninstall-All
    return
}

$ResolvedRef = Resolve-InstallRef
Write-Log "Installing $PkgName (CLI $BinaryName | MCP: $BinaryName mcp)"
Write-Log "Ref: $ResolvedRef — CI wheel preferred; bootstraps uv if needed"
Install-Package -Ref $ResolvedRef

# uv tools land in ~/.local/bin on Windows
Ensure-DirOnPath (Join-Path $env:USERPROFILE ".local\bin")
Refresh-CommandPath

if (-not $NoMcp) {
    Configure-AllMcpProviders
} else {
    Write-Log "Skipped MCP auto-config (-NoMcp)"
}

$lkResolved = Resolve-LkSim
if ($Verify) {
    if (-not $lkResolved) { throw "$BinaryName not found after install (check PATH / open new PowerShell)" }
    & $lkResolved --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "$BinaryName --help failed" }
    Write-Log "Verified $BinaryName --help"
}

Write-Host ""
Write-Host "✓ $PkgName installed" -ForegroundColor Green
if ($lkResolved) {
    Write-Host "  CLI: $lkResolved"
    Write-Host "  MCP: $lkResolved mcp"
} else {
    Write-Host "  CLI: $BinaryName  (open a new PowerShell if command not found)"
}
Write-Host ""
Write-Host "  Quick start:"
Write-Host "    $BinaryName guide"
Write-Host "    $BinaryName init --root C:\path\to\target"
Write-Host "    $BinaryName web --root C:\path\to\target"
Write-Host "    $BinaryName mcp"
Write-Host ""
Write-Host "  Report player ships inside the CI wheel (no Node/pnpm required)."
Write-Host "  If '$BinaryName' is not found, open a new PowerShell (PATH refresh)."
Write-Host ""
