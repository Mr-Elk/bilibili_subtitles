param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Url,

    [Alias("output-root")]
    [string]$OutputRoot,

    [Alias("no-browser-cookies")]
    [switch]$NoBrowserCookies
)

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    [Console]::Error.WriteLine("Python environment not found. Run .\setup.ps1 first.")
    exit 1
}

$previousLocation = Get-Location
try {
    Set-Location -LiteralPath $PSScriptRoot
    $toolArguments = @($Url)
    if ($OutputRoot) {
        $toolArguments += "--output-root"
        $toolArguments += $OutputRoot
    }
    if ($NoBrowserCookies) {
        $toolArguments += "--no-browser-cookies"
    }
    $toolArguments += $args
    & $python -m bilibili_subtitles @toolArguments
    $toolExitCode = $LASTEXITCODE
} finally {
    Set-Location -LiteralPath $previousLocation
}
exit $toolExitCode
