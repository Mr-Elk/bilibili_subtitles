[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path (
        [Environment]::GetFolderPath("UserProfile")
    ) "bin\bili-subtitles"),

    [string]$OutputRoot,

    [ValidateRange(0, 1000)]
    [int]$MaxParts = 0
)

$ErrorActionPreference = "Stop"
$userTarget = [EnvironmentVariableTarget]::User

$toolRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$resolvedInstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$configuredOutputRoot = if ($OutputRoot) {
    $OutputRoot
} else {
    [Environment]::GetEnvironmentVariable(
        "BILIBILI_SUBTITLE_OUTPUT_ROOT",
        $userTarget
    )
}
$resolvedOutputRoot = if ($configuredOutputRoot) {
    [System.IO.Path]::GetFullPath($configuredOutputRoot)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $toolRoot "output"))
}
$effectiveMaxParts = $MaxParts
if ($effectiveMaxParts -eq 0) {
    $configuredMaxParts = [Environment]::GetEnvironmentVariable(
        "BILIBILI_SUBTITLE_MAX_PARTS",
        $userTarget
    )
    if ($configuredMaxParts) {
        $parsedMaxParts = 0
        if (
            -not [int]::TryParse($configuredMaxParts, [ref]$parsedMaxParts) -or
            $parsedMaxParts -lt 1 -or
            $parsedMaxParts -gt 1000
        ) {
            throw "BILIBILI_SUBTITLE_MAX_PARTS must be an integer from 1 to 1000."
        }
        $effectiveMaxParts = $parsedMaxParts
    } else {
        $effectiveMaxParts = 20
    }
}
$launcherSource = Join-Path $toolRoot "bili-subtitles.cmd"
$scriptSource = Join-Path $toolRoot "bilibili-subtitles.ps1"
foreach ($source in @($launcherSource, $scriptSource)) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required launcher is missing: $source"
    }
}

if (-not (Test-Path -LiteralPath $resolvedInstallRoot -PathType Container)) {
    $null = New-Item -ItemType Directory -Path $resolvedInstallRoot
}
$launcherTarget = Join-Path $resolvedInstallRoot "bili-subtitles.cmd"
$scriptTarget = Join-Path $resolvedInstallRoot "bilibili-subtitles.ps1"
Copy-Item -LiteralPath $launcherSource -Destination $launcherTarget -Force
Copy-Item -LiteralPath $scriptSource -Destination $scriptTarget -Force

foreach ($pair in @(
    @($launcherSource, $launcherTarget),
    @($scriptSource, $scriptTarget)
)) {
    $sourceHash = (Get-FileHash -LiteralPath $pair[0] -Algorithm SHA256).Hash
    $targetHash = (Get-FileHash -LiteralPath $pair[1] -Algorithm SHA256).Hash
    if ($sourceHash -ne $targetHash) {
        throw "Installed launcher verification failed: $($pair[1])"
    }
}

$configTarget = Join-Path $resolvedInstallRoot "bili-subtitles.config.json"
$configTemporary = Join-Path $resolvedInstallRoot (
    ".bili-subtitles.config.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
)
$configData = [ordered]@{
    schema_version = 1
    tool_root = $toolRoot
    output_root = $resolvedOutputRoot
    max_parts = $effectiveMaxParts
}
$configJson = $configData | ConvertTo-Json -Compress
$strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
try {
    [System.IO.File]::WriteAllText($configTemporary, $configJson, $strictUtf8)
    if (Test-Path -LiteralPath $configTarget -PathType Leaf) {
        [System.IO.File]::Replace($configTemporary, $configTarget, $null)
    } else {
        [System.IO.File]::Move($configTemporary, $configTarget)
    }
} finally {
    if (Test-Path -LiteralPath $configTemporary -PathType Leaf) {
        Remove-Item -LiteralPath $configTemporary -Force
    }
}
$verifiedConfig = (
    [System.IO.File]::ReadAllText($configTarget, $strictUtf8) |
        ConvertFrom-Json
)
if (
    [int]$verifiedConfig.schema_version -ne 1 -or
    [string]$verifiedConfig.tool_root -ne $toolRoot -or
    [string]$verifiedConfig.output_root -ne $resolvedOutputRoot -or
    [int]$verifiedConfig.max_parts -ne $effectiveMaxParts
) {
    throw "Installed configuration verification failed: $configTarget"
}

[Environment]::SetEnvironmentVariable(
    "BILIBILI_SUBTITLE_TOOL_ROOT",
    $toolRoot,
    $userTarget
)
if ($OutputRoot) {
    [Environment]::SetEnvironmentVariable(
        "BILIBILI_SUBTITLE_OUTPUT_ROOT",
        $resolvedOutputRoot,
        $userTarget
    )
}
if ($MaxParts -gt 0) {
    [Environment]::SetEnvironmentVariable(
        "BILIBILI_SUBTITLE_MAX_PARTS",
        $MaxParts.ToString(),
        $userTarget
    )
}

$userPath = [Environment]::GetEnvironmentVariable("Path", $userTarget)
$pathParts = [System.Collections.Generic.List[string]]::new()
if ($userPath) {
    foreach ($part in $userPath.Split([System.IO.Path]::PathSeparator)) {
        if ($part.Trim()) {
            [void]$pathParts.Add($part.Trim())
        }
    }
}
$pathExists = $false
foreach ($part in $pathParts) {
    try {
        if ([System.IO.Path]::GetFullPath($part).Equals(
            $resolvedInstallRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            $pathExists = $true
            break
        }
    } catch {
        continue
    }
}
if (-not $pathExists) {
    [void]$pathParts.Add($resolvedInstallRoot)
    [Environment]::SetEnvironmentVariable(
        "Path",
        ($pathParts.ToArray() -join [System.IO.Path]::PathSeparator),
        $userTarget
    )
}

if (-not (($env:Path.Split([System.IO.Path]::PathSeparator)) -contains $resolvedInstallRoot)) {
    $env:Path = $resolvedInstallRoot + [System.IO.Path]::PathSeparator + $env:Path
}

try {
    if (-not ("BilibiliSubtitleEnvironmentBroadcast" -as [type])) {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class BilibiliSubtitleEnvironmentBroadcast {
    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(
        IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
        uint flags, uint timeout, out UIntPtr result);
}
"@
    }
    $broadcastResult = [UIntPtr]::Zero
    $null = [BilibiliSubtitleEnvironmentBroadcast]::SendMessageTimeout(
        [IntPtr]0xffff,
        0x001A,
        [UIntPtr]::Zero,
        "Environment",
        0x0002,
        5000,
        [ref]$broadcastResult
    )
} catch {
    Write-Warning "Installed successfully, but other open terminals may need to be restarted."
}

Write-Output "Installed command: $launcherTarget"
Write-Output "Installed config: $configTarget"
Write-Output "Canonical tool: $toolRoot"
Write-Output "Default output: $resolvedOutputRoot"
Write-Output "Large-anthology threshold: $effectiveMaxParts parts"
Write-Output "Run now or in any terminal: bili-subtitles --help"
