<#
.SYNOPSIS
提取 B 站已有字幕，并以限长方式查看本地字幕。

.DESCRIPTION
默认匿名访问，不读取浏览器登录信息。链接中的 ?p=N 或 -Page N 只处理
指定分 P。本脚本只负责定位运行环境和转发参数；字幕提取与本地读取逻辑
均由可测试的 bilibili_subtitles Python 包实现。

.EXAMPLE
.\bilibili-subtitles.ps1 "https://www.bilibili.com/video/BVxxxxxxxxxx?p=3"

.EXAMPLE
.\bilibili-subtitles.ps1 -Action Search -Target ".\output\BVxxxxxxxxxx" -Query "关键词"
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Url,

    [ValidateSet("Extract", "Status", "Inventory", "Map", "Search", "Slice")]
    [string]$Action = "Extract",

    [string]$Target,
    [string]$Query,
    [string]$Start = "00:00:00",
    [string]$End,

    [ValidateRange(0, 50)]
    [int]$Context = 1,

    [ValidateRange(1, 1000)]
    [int]$MaxResults = 8,

    [ValidateRange(200, 1000000)]
    [int]$ChunkChars = 5000,

    [int]$MaxChars = 0,

    [ValidateSet("Text", "Json")]
    [string]$Format = "Text",

    [Alias("output-root")]
    [string]$OutputRoot,

    [Alias("no-browser-cookies")]
    [switch]$NoBrowserCookies,

    [Alias("use-browser-cookies")]
    [switch]$UseBrowserCookies,

    [ValidateRange(1, 10000)]
    [int]$Page,

    [switch]$AllParts,

    [ValidateRange(0, 1000)]
    [int]$MaxParts = 0,

    [Alias("help", "h")]
    [switch]$ShowHelp,

    [string]$ToolRoot
)

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$script:InstalledConfigLoaded = $false
$script:InstalledConfig = $null

function Show-Usage {
    @"
Bilibili subtitle utility

Extract:
  bili-subtitles <BV URL>                    Extract captions anonymously
  bili-subtitles <BV URL>?p=3                Extract only part 3
  bili-subtitles <BV URL> -Page 3             Extract only part 3
  bili-subtitles <BV URL> -AllParts           Confirm extraction of a large anthology
  bili-subtitles <BV URL> -UseBrowserCookies  Opt in to Chrome login state

Read local captions with bounded output:
  bili-subtitles -Action Status    -Target <BV-output-directory>
  bili-subtitles -Action Inventory -Target <file-or-directory>
  bili-subtitles -Action Map       -Target <file-or-directory>
  bili-subtitles -Action Search    -Target <file-or-directory> -Query <text>
  bili-subtitles -Action Slice     -Target <file-or-directory> -Start HH:MM:SS [-End HH:MM:SS]

Common options:
  -OutputRoot <directory>  Override the extraction output directory
  -MaxParts <number>       Large-anthology threshold (default: 20)
  -MaxChars <number>       Limit text printed to the terminal
  -Format Json             Emit stable JSON for local read actions
  --help, -h, -?           Show this help
"@ | Write-Output
}

function Write-BoundedLines {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Lines,

        [Parameter(Mandatory = $true)]
        [int]$Limit
    )

    $newLine = [Environment]::NewLine
    $rendered = (($Lines | ForEach-Object { [string]$_ }) -join $newLine).TrimEnd()
    if ($rendered.Length -gt $Limit) {
        $marker = $newLine + "... [truncated; narrow the request or raise -MaxChars]"
        $prefixLength = [Math]::Max(0, $Limit - $marker.Length)
        $rendered = $rendered.Substring(0, $prefixLength) + $marker
    }
    if ($rendered) {
        [Console]::Out.WriteLine($rendered)
    }
}

function Get-InstalledConfig {
    if ($script:InstalledConfigLoaded) {
        return $script:InstalledConfig
    }
    $script:InstalledConfigLoaded = $true
    $configPath = Join-Path $PSScriptRoot "bili-subtitles.config.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return $null
    }
    $configInfo = Get-Item -LiteralPath $configPath
    if ($configInfo.Length -gt 65536) {
        throw "Installed configuration is too large: $configPath"
    }
    try {
        $strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
        $configText = [System.IO.File]::ReadAllText($configPath, $strictUtf8)
        $parsed = $configText | ConvertFrom-Json
    } catch {
        throw "Installed configuration is invalid: $configPath"
    }
    $schemaVersion = 0
    if (
        -not [int]::TryParse(
            [string]$parsed.schema_version,
            [ref]$schemaVersion
        ) -or
        $schemaVersion -ne 1
    ) {
        throw "Unsupported installed configuration version: $configPath"
    }
    if (-not ([string]$parsed.tool_root).Trim()) {
        throw "Installed configuration is missing tool_root: $configPath"
    }
    $script:InstalledConfig = $parsed
    return $script:InstalledConfig
}

function Resolve-MaxParts {
    if ($MaxParts -gt 0) {
        return $MaxParts
    }
    $configured = [Environment]::GetEnvironmentVariable(
        "BILIBILI_SUBTITLE_MAX_PARTS"
    )
    if (-not $configured) {
        $installedConfig = Get-InstalledConfig
        if ($installedConfig -and $null -ne $installedConfig.max_parts) {
            $configured = [string]$installedConfig.max_parts
        }
    }
    if (-not $configured) {
        return 20
    }
    $parsed = 0
    if (
        -not [int]::TryParse($configured, [ref]$parsed) -or
        $parsed -lt 1 -or
        $parsed -gt 1000
    ) {
        throw "BILIBILI_SUBTITLE_MAX_PARTS must be an integer from 1 to 1000."
    }
    return $parsed
}

function Resolve-ToolRoot {
    $effectiveToolRoot = $ToolRoot
    if (-not $effectiveToolRoot) {
        $effectiveToolRoot = [Environment]::GetEnvironmentVariable(
            "BILIBILI_SUBTITLE_TOOL_ROOT"
        )
    }
    if (-not $effectiveToolRoot) {
        $installedConfig = Get-InstalledConfig
        if ($installedConfig) {
            $effectiveToolRoot = [string]$installedConfig.tool_root
        }
    }
    if (-not $effectiveToolRoot) {
        $effectiveToolRoot = $PSScriptRoot
    }
    try {
        $resolved = [System.IO.Path]::GetFullPath($effectiveToolRoot)
    } catch {
        throw "Subtitle tool path is invalid: $effectiveToolRoot"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $resolved "bilibili_subtitles") -PathType Container)) {
        throw (
            "Subtitle tool not found at $resolved. Set " +
            "BILIBILI_SUBTITLE_TOOL_ROOT, reinstall the command, or pass -ToolRoot."
        )
    }
    return $resolved
}

function Resolve-ToolRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$ResolvedToolRoot,
        [Parameter(Mandatory = $true)][bool]$RequireExtractorDependencies
    )

    $venvPython = Join-Path $ResolvedToolRoot ".venv\Scripts\python.exe"
    $candidates = [System.Collections.Generic.List[string]]::new()
    [void]$candidates.Add($venvPython)
    $configuredPython = [Environment]::GetEnvironmentVariable(
        "BILIBILI_SUBTITLE_PYTHON"
    )
    if ($configuredPython) {
        [void]$candidates.Add($configuredPython)
    }

    $runtimePythonPath = $ResolvedToolRoot
    $originalPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH")
    $importCheck = if ($RequireExtractorDependencies) {
        "import bilibili_subtitles, yt_dlp; raise SystemExit(0)"
    } else {
        "import bilibili_subtitles; raise SystemExit(0)"
    }

    try {
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $runtimePythonPath)
        $seen = [System.Collections.Generic.HashSet[string]]::new(
            [System.StringComparer]::OrdinalIgnoreCase
        )
        foreach ($candidate in $candidates) {
            if (-not $candidate) {
                continue
            }
            try {
                $candidatePath = [System.IO.Path]::GetFullPath($candidate)
            } catch {
                continue
            }
            if (-not $seen.Add($candidatePath)) {
                continue
            }
            if (-not (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
                continue
            }
            try {
                $null = & $candidatePath -c $importCheck 2>$null
                if ($LASTEXITCODE -eq 0) {
                    return [pscustomobject]@{
                        Python = $candidatePath
                        PythonPath = $runtimePythonPath
                        PreferredPython = $venvPython
                    }
                }
            } catch {
                continue
            }
        }
    } finally {
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $originalPythonPath)
    }

    $dependencyHint = if ($RequireExtractorDependencies) {
        "the subtitle package and yt-dlp"
    } else {
        "the subtitle package"
    }
    throw (
        "No usable Python runtime can import $dependencyHint. Run setup.ps1 " +
        "in $ResolvedToolRoot, or set BILIBILI_SUBTITLE_PYTHON."
    )
}

function New-ToolArguments {
    $arguments = @("-m", "bilibili_subtitles")
    if ($Action -eq "Extract") {
        if (-not $Url) {
            throw "Extract requires a direct Bilibili BV URL."
        }
        $arguments += $Url
        $effectiveOutputRoot = $OutputRoot
        if (-not $effectiveOutputRoot) {
            $effectiveOutputRoot = [Environment]::GetEnvironmentVariable(
                "BILIBILI_SUBTITLE_OUTPUT_ROOT"
            )
        }
        if (-not $effectiveOutputRoot) {
            $installedConfig = Get-InstalledConfig
            if ($installedConfig -and $installedConfig.output_root) {
                $effectiveOutputRoot = [string]$installedConfig.output_root
            }
        }
        if ($effectiveOutputRoot) {
            $arguments += @("--output-root", $effectiveOutputRoot)
        }
        if ($Page -gt 0) {
            $arguments += @("--page", $Page.ToString())
        }
        if ($AllParts) {
            $arguments += "--all-parts"
        }
        $arguments += @("--max-parts", (Resolve-MaxParts).ToString())
        if ($UseBrowserCookies) {
            $arguments += "--use-browser-cookies"
        } else {
            $arguments += "--no-browser-cookies"
        }
        return $arguments
    }

    if (-not $Target) {
        throw "$Action requires -Target pointing to a transcript file or BV output directory."
    }
    $arguments += @("--action", $Action.ToLowerInvariant(), "--target", $Target)
    $arguments += @("--context", $Context.ToString())
    $arguments += @("--max-results", $MaxResults.ToString())
    $arguments += @("--chunk-chars", $ChunkChars.ToString())
    $arguments += @("--max-chars", $MaxChars.ToString())
    $arguments += @("--format", $Format.ToLowerInvariant())
    $arguments += @("--start", $Start)
    if ($Query) {
        $arguments += @("--query", $Query)
    }
    if ($End) {
        $arguments += @("--end", $End)
    }
    return $arguments
}

function Invoke-SubtitleTool {
    $resolvedToolRoot = Resolve-ToolRoot
    $isExtraction = $Action -eq "Extract"
    $runtime = Resolve-ToolRuntime `
        -ResolvedToolRoot $resolvedToolRoot `
        -RequireExtractorDependencies $isExtraction
    $toolArguments = @(New-ToolArguments)

    $originalPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH")
    $originalPythonIoEncoding = [Environment]::GetEnvironmentVariable(
        "PYTHONIOENCODING"
    )
    $originalErrorActionPreference = $ErrorActionPreference
    $previousLocation = Get-Location
    try {
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $runtime.PythonPath)
        [Environment]::SetEnvironmentVariable("PYTHONIOENCODING", "utf-8")
        Set-Location -LiteralPath $resolvedToolRoot
        $ErrorActionPreference = "Continue"
        $capturedOutput = @(& $runtime.Python @toolArguments 2>&1)
        $toolExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $originalErrorActionPreference
        Set-Location -LiteralPath $previousLocation
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $originalPythonPath)
        [Environment]::SetEnvironmentVariable(
            "PYTHONIOENCODING",
            $originalPythonIoEncoding
        )
    }
    $capturedOutput = @($capturedOutput | ForEach-Object { $_.ToString() })

    if (-not $runtime.Python.Equals(
        $runtime.PreferredPython,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        $runtimeWarning = "Warning: using fallback Python runtime: $($runtime.Python)"
        if (-not $isExtraction -and $Format -eq "Json") {
            [Console]::Error.WriteLine($runtimeWarning)
        } else {
            $capturedOutput = @($runtimeWarning) + $capturedOutput
        }
    }

    if ($toolExitCode -eq 0 -and $isExtraction) {
        $selectedOutput = @(
            $capturedOutput | Where-Object {
                $_ -cmatch "^(Warning:|Extracted\s|Output:)"
            }
        )
        if ($selectedOutput.Count -eq 0) {
            $selectedOutput = @($capturedOutput | Select-Object -Last 20)
        }
    } elseif ($toolExitCode -eq 0) {
        $selectedOutput = $capturedOutput
    } else {
        $selectedOutput = @($capturedOutput | Select-Object -Last 20)
    }
    if ($selectedOutput.Count -eq 0) {
        $selectedOutput = @("Subtitle tool exited with code $toolExitCode.")
    }
    if ($toolExitCode -eq 0 -and -not $isExtraction -and $Format -eq "Json") {
        [Console]::Out.WriteLine(
            (($selectedOutput | ForEach-Object { [string]$_ }) -join "")
        )
    } else {
        $outputLimit = if ($MaxChars -gt 0) { $MaxChars } else { 10000 }
        Write-BoundedLines -Lines $selectedOutput -Limit $outputLimit
    }
    return $toolExitCode
}

try {
    if ($ShowHelp) {
        Show-Usage
        exit 0
    }
    if ($MaxChars -ne 0 -and $MaxChars -lt 200) {
        throw "MaxChars must be 0 (use the action default) or at least 200."
    }
    if ($Action -eq "Extract" -and $Format -ne "Text") {
        throw "Format Json is only supported for local read actions."
    }
    if ($NoBrowserCookies -and $UseBrowserCookies) {
        throw "NoBrowserCookies and UseBrowserCookies cannot be used together."
    }
    if ($Page -gt 0 -and $AllParts) {
        throw "Page and AllParts cannot be used together."
    }
    exit (Invoke-SubtitleTool)
} catch {
    [Console]::Error.WriteLine("Error: $($_.Exception.Message)")
    exit 1
}
