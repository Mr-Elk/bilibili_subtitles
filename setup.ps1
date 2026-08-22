[CmdletBinding()]
param(
    [string]$Python,
    [switch]$Repair
)

$ErrorActionPreference = "Stop"

function Resolve-PythonCandidate {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        return [System.IO.Path]::GetFullPath($Candidate)
    }
    $command = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }
    return $null
}

function Get-SupportedPython {
    param([string]$RequestedPython)

    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($RequestedPython) {
        [void]$candidates.Add($RequestedPython)
    }
    $configuredPython = [Environment]::GetEnvironmentVariable(
        "BILIBILI_SUBTITLE_BOOTSTRAP_PYTHON"
    )
    if ($configuredPython) {
        [void]$candidates.Add($configuredPython)
    }
    foreach ($name in @("python.exe", "python3.exe")) {
        [void]$candidates.Add($name)
    }
    $userProfile = [Environment]::GetEnvironmentVariable("USERPROFILE")
    if ($userProfile) {
        [void]$candidates.Add((Join-Path $userProfile (
            ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
        )))
    }

    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::OrdinalIgnoreCase
    )
    foreach ($candidate in $candidates) {
        $resolved = Resolve-PythonCandidate $candidate
        if (-not $resolved -or -not $seen.Add($resolved)) {
            continue
        }
        try {
            $versionText = (& $resolved -c (
                "import sys; print(str(sys.version_info.major)+'.'+" +
                "str(sys.version_info.minor))"
            ) 2>$null).Trim()
            if ($LASTEXITCODE -ne 0) {
                continue
            }
            $version = [version]$versionText
            if ($version -ge [version]"3.11" -and $version -lt [version]"3.14") {
                return $resolved
            }
        } catch {
            continue
        }
    }

    throw (
        "No supported Python was found. Install Python 3.11-3.13, pass " +
        "-Python <python.exe>, or set BILIBILI_SUBTITLE_BOOTSTRAP_PYTHON."
    )
}

function Test-VenvPython {
    param([Parameter(Mandatory = $true)][string]$Candidate)

    if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return $false
    }
    try {
        $null = & $Candidate -c (
            "import sys; ok=(3,11) <= sys.version_info[:2] < (3,14); " +
            "raise SystemExit(0 if ok else 1)"
        ) 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$toolRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$venv = [System.IO.Path]::GetFullPath((Join-Path $toolRoot ".venv"))
if ([System.IO.Path]::GetDirectoryName($venv) -ne $toolRoot) {
    throw "Refusing to manage a virtual environment outside the tool directory."
}
$venvPython = Join-Path $venv "Scripts\python.exe"
$requirements = Join-Path $toolRoot "requirements-lock.txt"
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "Dependency lock is missing: $requirements"
}
$tests = Join-Path $toolRoot "tests"
$backup = $null
$createdNewVenv = $false

$venvHealthy = Test-VenvPython $venvPython
if (-not $venvHealthy -and (Test-Path -LiteralPath $venv)) {
    if (-not $Repair) {
        throw (
            "The existing .venv is missing or broken. Run .\setup.ps1 -Repair " +
            "to replace it while keeping a timestamped backup."
        )
    }
    $backup = "$venv.repair-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    if (Test-Path -LiteralPath $backup) {
        throw "Repair backup path already exists: $backup"
    }
    Move-Item -LiteralPath $venv -Destination $backup
}

try {
    if (-not (Test-VenvPython $venvPython)) {
        $bootstrapPython = Get-SupportedPython $Python
        Write-Output "Creating .venv with $bootstrapPython"
        $createdNewVenv = $true
        & $bootstrapPython -m venv $venv
        if ($LASTEXITCODE -ne 0 -or -not (Test-VenvPython $venvPython)) {
            throw "Failed to create a working Python virtual environment."
        }
    }

    & $venvPython -m pip install --disable-pip-version-check --require-virtualenv -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the pinned dependencies."
    }

    & $venvPython -m unittest discover -s $tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "The environment was created, but the test suite failed."
    }
} catch {
    $failure = $_
    if ($createdNewVenv -and (Test-Path -LiteralPath $venv)) {
        Remove-Item -LiteralPath $venv -Recurse -Force
    }
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $venv
    }
    throw $failure
}

$version = (& $venvPython -c "import sys; print(sys.version.split()[0])").Trim()
Write-Output "Setup complete (Python $version)."
if ($backup) {
    Write-Output "The previous broken environment was kept at: $backup"
}
Write-Output "Run .\bili-subtitles.cmd --help for usage."
