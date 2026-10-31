<#
.SYNOPSIS
    Crawl all sources, import ImmoScout24 alert emails, crawl ThinkImmo, fetch listing
    details and enrich.
    Used by the scheduled task.
.PARAMETER MaxPages
    Page limit per source. Keep it high enough to reach the last page, otherwise listings
    that disappeared can't be marked as removed.
.PARAMETER ThinkImmo
    Crawl ThinkImmo after the portals, so its properties can link to their listings.
.PARAMETER ThinkImmoDetailLimit
    Max ThinkImmo exposés per run.
#>
param(
    [int]$MaxPages = 400,
    [string[]]$Sources = @("kleinanzeigen", "immowelt", "neubaukompass", "immoscout24"),
    [bool]$ThinkImmo = $true,
    [int]$ThinkImmoDetailLimit = 300,
    [int]$KeepLogDays = 30
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("run-{0:yyyyMMdd-HHmm}.log" -f (Get-Date))

function Write-Log([string]$message) {
    "[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $message | Tee-Object -FilePath $log -Append
}

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $uv = Get-ChildItem "$env:APPDATA\Python\Python3*\Scripts\uv.exe", "$env:USERPROFILE\.local\bin\uv.exe" `
        -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $uv) { Write-Log "uv not found"; exit 1 }

$pg = Get-Service "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pg -and $pg.Status -ne "Running") {
    Write-Log "Starting $($pg.Name)"
    Start-Service $pg.Name
}

$failed = @()
foreach ($source in $Sources) {
    Write-Log "Crawling $source"
    & $uv run immo crawl $source --max-pages $MaxPages --no-enrich *>> $log
    if ($LASTEXITCODE -ne 0) { $failed += $source }
}

$emailDir = Join-Path $root "data\is24-emails"
if (Get-ChildItem $emailDir -Filter *.eml -ErrorAction SilentlyContinue) {
    Write-Log "Importing ImmoScout24 alert emails"
    & $uv run immo import-is24-emails --no-enrich *>> $log
}

if ($ThinkImmo) {
    Write-Log "Crawling thinkimmo"
    & $uv run immo crawl-thinkimmo --detail-limit $ThinkImmoDetailLimit --no-enrich *>> $log
    if ($LASTEXITCODE -ne 0) { $failed += "thinkimmo" }
}

Write-Log "Fetching listing details"
& $uv run immo details --limit 300 *>> $log
if ($LASTEXITCODE -ne 0) { $failed += "details" }

Write-Log "Enriching"
& $uv run immo enrich *>> $log
if ($LASTEXITCODE -ne 0) { $failed += "enrich" }

Get-ChildItem $logDir -Filter "run-*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$KeepLogDays) } |
    Remove-Item

if ($failed.Count) { Write-Log ("Finished with failures: " + ($failed -join ", ")); exit 1 }
Write-Log "Finished"
