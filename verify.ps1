[CmdletBinding()]
param(
    [string]$Python,
    [switch]$RequireClean
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)."
    }
}

$toolRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$pythonPath = if ($Python) {
    [System.IO.Path]::GetFullPath($Python)
} else {
    Join-Path $toolRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Verification Python was not found: $pythonPath"
}

$requiredFiles = @(
    "GOVERNANCE.md",
    "README.md",
    "READER_JSON_V1.md",
    "requirements.txt",
    "requirements-lock.txt",
    "setup.ps1",
    "install.ps1",
    "bilibili-subtitles.ps1",
    "verify.ps1"
)
foreach ($relativePath in $requiredFiles) {
    $requiredPath = Join-Path $toolRoot $relativePath
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required governed file is missing: $relativePath"
    }
}

$originalPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH")
try {
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $toolRoot)
    Invoke-Checked -FilePath $pythonPath -Arguments @(
        "-c",
        "import sys, bilibili_subtitles, yt_dlp; ok=(3,11) <= sys.version_info[:2] < (3,14); raise SystemExit(0 if ok else 1)"
    ) -FailureMessage "Python or required imports are not usable"
    Invoke-Checked -FilePath $pythonPath -Arguments @(
        "-m", "pip", "check"
    ) -FailureMessage "Installed dependencies are inconsistent"
    $lockMismatches = [System.Collections.Generic.List[string]]::new()
    foreach ($rawLine in Get-Content -LiteralPath (
        Join-Path $toolRoot "requirements-lock.txt"
    )) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $parts = $line.Split(@("=="), 2, [System.StringSplitOptions]::None)
        if ($parts.Count -ne 2) {
            throw "Unsupported dependency lock entry: $line"
        }
        $packageName = [regex]::Replace($parts[0].Trim(), "\[.*\]$", "")
        $expectedVersion = $parts[1].Trim()
        $installedVersion = (& $pythonPath -c (
            "from importlib.metadata import version; import sys; " +
            "print(version(sys.argv[1]))"
        ) $packageName).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Cannot inspect locked dependency: $packageName"
        }
        if ($installedVersion -ne $expectedVersion) {
            [void]$lockMismatches.Add(
                "${packageName}: expected $expectedVersion, installed $installedVersion"
            )
        }
    }
    if ($lockMismatches.Count -gt 0) {
        throw (
            "Installed dependencies do not match requirements-lock.txt: " +
            ($lockMismatches -join "; ")
        )
    }
    Invoke-Checked -FilePath $pythonPath -Arguments @(
        "-m", "unittest", "discover", "-s", (Join-Path $toolRoot "tests"), "-v"
    ) -FailureMessage "Test suite failed"
    Invoke-Checked -FilePath $pythonPath -Arguments @(
        "-m", "compileall", "-q",
        (Join-Path $toolRoot "bilibili_subtitles"),
        (Join-Path $toolRoot "tests")
    ) -FailureMessage "Python compilation failed"
} finally {
    [Environment]::SetEnvironmentVariable("PYTHONPATH", $originalPythonPath)
}

$scriptFiles = @(
    "setup.ps1",
    "install.ps1",
    "bilibili-subtitles.ps1",
    "extract.ps1",
    "verify.ps1"
)
foreach ($relativePath in $scriptFiles) {
    $tokens = $null
    $errors = $null
    $scriptPath = Join-Path $toolRoot $relativePath
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        $messages = ($errors | ForEach-Object { $_.Message }) -join "; "
        throw "PowerShell syntax failed for ${relativePath}: $messages"
    }
}

$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) {
    throw "Git is required for tracked-file governance checks."
}
$safeRoot = $toolRoot.Replace("\", "/")
$gitArguments = @("-c", "safe.directory=$safeRoot", "-C", $toolRoot)
$trackedFiles = @(& $git @gitArguments ls-files)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot enumerate tracked files."
}
$forbiddenTrackedPatterns = @(
    '(^|/)(output|\.course-learning-private|\.venv)(/|$)',
    '(^|/)\.venv\.repair-backup-[^/]+(/|$)',
    '(^|/)\.env(\..*)?$',
    '(^|/).*\.(key|pem)$',
    '(^|/)part-[0-9]+\.md$',
    '(^|/)(request|model-output)\.json$'
)
$violations = foreach ($trackedFile in $trackedFiles) {
    $normalized = $trackedFile.Replace("\", "/")
    foreach ($pattern in $forbiddenTrackedPatterns) {
        if ($normalized -match $pattern) {
            $trackedFile
            break
        }
    }
}
if ($violations) {
    throw "Private or runtime artifacts are tracked: $($violations -join ', ')"
}

$privateKeyMarker = "-----BEGIN " + "PRIVATE KEY-----"
$authorizationPattern = '(?im)^\s*authorization\s*:\s*bearer\s+\S+'
$secretAssignmentPattern = '(?im)^\s*(api[_-]?key|token)\s*[:=]\s*["'']?[A-Za-z0-9_-]{16,}'
$secretViolations = foreach ($trackedFile in $trackedFiles) {
    $trackedPath = Join-Path $toolRoot $trackedFile
    if (-not (Test-Path -LiteralPath $trackedPath -PathType Leaf)) {
        continue
    }
    try {
        $trackedText = [System.IO.File]::ReadAllText($trackedPath)
    } catch {
        continue
    }
    if (
        $trackedText.Contains($privateKeyMarker) -or
        $trackedText -match $authorizationPattern -or
        $trackedText -match $secretAssignmentPattern
    ) {
        $trackedFile
    }
}
if ($secretViolations) {
    throw "Possible credentials are present in tracked files: $($secretViolations -join ', ')"
}

& $git @gitArguments diff --check
if ($LASTEXITCODE -ne 0) {
    throw "Git diff check failed."
}
& $git @gitArguments diff --cached --check
if ($LASTEXITCODE -ne 0) {
    throw "Staged Git diff check failed."
}
if ($RequireClean) {
    $status = @(& $git @gitArguments status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot inspect Git status."
    }
    if ($status.Count -gt 0) {
        throw "Working tree is not clean; refusing a governed snapshot."
    }
}

Write-Output "[verify] PASS"
