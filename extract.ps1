<#
Backward-compatible entry point. New usage should prefer bili-subtitles.cmd or
bilibili-subtitles.ps1 so extraction and bounded local reading share one CLI.
#>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Url,

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
    [int]$MaxParts = 0
)

$launcher = Join-Path $PSScriptRoot "bilibili-subtitles.ps1"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "Main launcher not found. Restore bilibili-subtitles.ps1 or reinstall the tool."
    )
    exit 1
}

$launcherArguments = @($Url)
if ($OutputRoot) {
    $launcherArguments += "-OutputRoot"
    $launcherArguments += $OutputRoot
}
if ($NoBrowserCookies) {
    $launcherArguments += "-NoBrowserCookies"
}
if ($UseBrowserCookies) {
    $launcherArguments += "-UseBrowserCookies"
}
if ($Page -gt 0) {
    $launcherArguments += "-Page"
    $launcherArguments += $Page.ToString()
}
if ($AllParts) {
    $launcherArguments += "-AllParts"
}
if ($MaxParts -gt 0) {
    $launcherArguments += "-MaxParts"
    $launcherArguments += $MaxParts.ToString()
}
$launcherArguments += $args

& $launcher @launcherArguments
exit $LASTEXITCODE
