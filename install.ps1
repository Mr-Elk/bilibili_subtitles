[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path (
        [Environment]::GetFolderPath("UserProfile")
    ) "bin\bili-subtitles"),

    [string]$OutputRoot,

    [ValidateRange(0, 1000)]
    [int]$MaxParts = 0,

    [switch]$NoUserEnvironment
)

$ErrorActionPreference = "Stop"
$userTarget = [EnvironmentVariableTarget]::User

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $stream = [System.IO.File]::OpenRead($LiteralPath)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString(
            $hasher.ComputeHash($stream)
        ).Replace("-", "")
    } finally {
        $hasher.Dispose()
        $stream.Dispose()
    }
}

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

$installRootExisted = Test-Path -LiteralPath $resolvedInstallRoot -PathType Container
if (-not $installRootExisted) {
    $null = New-Item -ItemType Directory -Path $resolvedInstallRoot
}
$launcherTarget = Join-Path $resolvedInstallRoot "bili-subtitles.cmd"
$scriptTarget = Join-Path $resolvedInstallRoot "bilibili-subtitles.ps1"
$configTarget = Join-Path $resolvedInstallRoot "bili-subtitles.config.json"
$configData = [ordered]@{
    schema_version = 1
    tool_root = $toolRoot
    output_root = $resolvedOutputRoot
    max_parts = $effectiveMaxParts
}
$configJson = $configData | ConvertTo-Json -Compress
$strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
$installNonce = "$PID.$([Guid]::NewGuid().ToString('N'))"
function New-InstallItem {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )

    return [pscustomobject]@{
        Name = $Name
        Source = $Source
        Target = $Target
        Temporary = Join-Path $resolvedInstallRoot (
            ".bili-subtitles.install.$installNonce.$Name.tmp"
        )
        Backup = Join-Path $resolvedInstallRoot (
            ".bili-subtitles.install.$installNonce.$Name.bak"
        )
        RollbackDiscard = Join-Path $resolvedInstallRoot (
            ".bili-subtitles.install.$installNonce.$Name.rollback.bak"
        )
        HadTarget = $false
    }
}

# Publish the backward-compatible schema v1 configuration first, then the
# PowerShell implementation, and switch the CMD entry point last. An existing
# launcher therefore never starts a new implementation against an old config.
$installItems = @(
    (New-InstallItem -Name "config" -Target $configTarget),
    (New-InstallItem -Name "script" -Source $scriptSource -Target $scriptTarget),
    (New-InstallItem -Name "launcher" -Source $launcherSource -Target $launcherTarget)
)
$publishedItems = [System.Collections.Generic.List[object]]::new()
$environmentNames = @(
    "BILIBILI_SUBTITLE_TOOL_ROOT",
    "BILIBILI_SUBTITLE_OUTPUT_ROOT",
    "BILIBILI_SUBTITLE_MAX_PARTS",
    "Path"
)
$userEnvironmentBefore = @{}
foreach ($name in $environmentNames) {
    $userEnvironmentBefore[$name] = [Environment]::GetEnvironmentVariable(
        $name,
        $userTarget
    )
}
$processPathBefore = $env:Path
$environmentUpdateStarted = $false
$installSucceeded = $false
$preserveRecoveryFiles = $false

try {
    foreach ($item in $installItems) {
        foreach ($transientPath in @(
            $item.Temporary,
            $item.Backup,
            $item.RollbackDiscard
        )) {
            if (Test-Path -LiteralPath $transientPath) {
                throw "Install transaction path already exists: $transientPath"
            }
        }
        if ($item.Source) {
            Copy-Item -LiteralPath $item.Source -Destination $item.Temporary
            $sourceHash = Get-FileSha256 -LiteralPath $item.Source
            $temporaryHash = Get-FileSha256 -LiteralPath $item.Temporary
            if ($sourceHash -ne $temporaryHash) {
                throw "Staged launcher verification failed: $($item.Name)"
            }
        } else {
            [System.IO.File]::WriteAllText(
                $item.Temporary,
                $configJson,
                $strictUtf8
            )
        }
    }

    foreach ($item in $installItems) {
        if (Test-Path -LiteralPath $item.Target -PathType Container) {
            throw "Install target is not a file: $($item.Target)"
        }
        $item.HadTarget = Test-Path -LiteralPath $item.Target -PathType Leaf
        [void]$publishedItems.Add($item)
        if ($item.HadTarget) {
            [System.IO.File]::Replace(
                $item.Temporary,
                $item.Target,
                $item.Backup
            )
        } else {
            [System.IO.File]::Move($item.Temporary, $item.Target)
        }
    }

    foreach ($pair in @(
        @($launcherSource, $launcherTarget),
        @($scriptSource, $scriptTarget)
    )) {
        $sourceHash = Get-FileSha256 -LiteralPath $pair[0]
        $targetHash = Get-FileSha256 -LiteralPath $pair[1]
        if ($sourceHash -ne $targetHash) {
            throw "Installed launcher verification failed: $($pair[1])"
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

    if (-not $NoUserEnvironment) {
        $environmentUpdateStarted = $true
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
            $env:Path = (
                $resolvedInstallRoot +
                [System.IO.Path]::PathSeparator +
                $env:Path
            )
        }
    }
    $installSucceeded = $true
} catch {
    $installFailure = $_
    $rollbackFailures = [System.Collections.Generic.List[string]]::new()

    if ($environmentUpdateStarted) {
        foreach ($name in $environmentNames) {
            try {
                [Environment]::SetEnvironmentVariable(
                    $name,
                    $userEnvironmentBefore[$name],
                    $userTarget
                )
            } catch {
                [void]$rollbackFailures.Add("user environment $name")
            }
        }
        $env:Path = $processPathBefore
    }

    for ($index = $publishedItems.Count - 1; $index -ge 0; $index--) {
        $item = $publishedItems[$index]
        try {
            if ($item.HadTarget) {
                if (Test-Path -LiteralPath $item.Backup -PathType Leaf) {
                    if (Test-Path -LiteralPath $item.Target -PathType Leaf) {
                        [System.IO.File]::Replace(
                            $item.Backup,
                            $item.Target,
                            $item.RollbackDiscard
                        )
                    } else {
                        [System.IO.File]::Move($item.Backup, $item.Target)
                    }
                }
            } elseif (Test-Path -LiteralPath $item.Target -PathType Leaf) {
                Remove-Item -LiteralPath $item.Target -Force
            }
        } catch {
            [void]$rollbackFailures.Add($item.Target)
        }
    }

    if ($rollbackFailures.Count -gt 0) {
        $preserveRecoveryFiles = $true
        throw (
            "Installation failed and rollback needs manual recovery for: " +
            ($rollbackFailures -join ", ") +
            ". Original failure: " +
            $installFailure.Exception.Message
        )
    }
    throw $installFailure
} finally {
    foreach ($item in $installItems) {
        if (Test-Path -LiteralPath $item.Temporary -PathType Leaf) {
            Remove-Item -LiteralPath $item.Temporary -Force
        }
        if (
            -not $preserveRecoveryFiles -and
            (Test-Path -LiteralPath $item.Backup -PathType Leaf)
        ) {
            Remove-Item -LiteralPath $item.Backup -Force
        }
        if (
            -not $preserveRecoveryFiles -and
            (Test-Path -LiteralPath $item.RollbackDiscard -PathType Leaf)
        ) {
            Remove-Item -LiteralPath $item.RollbackDiscard -Force
        }
    }
    if (
        -not $installSucceeded -and
        -not $installRootExisted -and
        (Test-Path -LiteralPath $resolvedInstallRoot -PathType Container) -and
        -not (Get-ChildItem -LiteralPath $resolvedInstallRoot -Force | Select-Object -First 1)
    ) {
        Remove-Item -LiteralPath $resolvedInstallRoot -Force
    }
}

if (-not $NoUserEnvironment) {
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
}

Write-Output "Installed command: $launcherTarget"
Write-Output "Installed config: $configTarget"
Write-Output "Canonical tool: $toolRoot"
Write-Output "Default output: $resolvedOutputRoot"
Write-Output "Large-anthology threshold: $effectiveMaxParts parts"
if ($NoUserEnvironment) {
    Write-Output "User environment: unchanged (-NoUserEnvironment)"
    Write-Output "Run directly: `"$launcherTarget`" --help"
} else {
    Write-Output "Run now or in any terminal: bili-subtitles --help"
}
