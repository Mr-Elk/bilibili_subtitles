$ErrorActionPreference = "Stop"

$venv = Join-Path $PSScriptRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"
$requirements = Join-Path $PSScriptRoot "requirements.txt"
$tests = Join-Path $PSScriptRoot "tests"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    & py -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python 3.11 virtual environment."
    }
}

$pythonVersion = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne "3.11") {
    throw "The existing .venv is not Python 3.11. Remove .venv and run setup.ps1 again."
}

& $python -m pip install --disable-pip-version-check --require-virtualenv -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the pinned dependencies."
}

& $python -m unittest discover -s $tests -v
if ($LASTEXITCODE -ne 0) {
    throw "Setup completed, but the test suite failed."
}

"Setup complete. Run .\extract.ps1 with a direct Bilibili BV URL."
