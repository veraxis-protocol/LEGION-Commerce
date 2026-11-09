# LEGION Deal Rescue - rebuild evidence-linked dossiers and serve the merchant UI (replay mode).
#   .\deal_rescue\run_deal_rescue.ps1 -Evidence .\wo004_evidence [-FreshRun -NimKeyFile <file>] [-Port 8795]
# Requires: Python 3.11+. -FreshRun additionally requires MG_FRESH checkouts at the pins (see README) and a NIM key.
# Never sends a PayPal request.
param([Parameter(Mandatory = $true)][string]$Evidence, [switch]$FreshRun, [string]$NimKeyFile, [int]$Port = 8795,
      [string]$MgRoot = "C:\Users\Administrator\Documents\ChatGPT\Hackathons\MG_FRESH",
      [string]$PaypalHistoric = "C:\Users\Administrator\Dropbox\PayPal\MARKET_GENESIS_20261107\WO-LMF-007\paused-run")
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Repo
python -B deal_rescue\tools\gate_zero.py $Evidence
if ($FreshRun) {
    if (-not (Test-Path $NimKeyFile)) { throw "NIM key file required for -FreshRun" }
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"; $out = Join-Path $Evidence "runs\A2-fresh-$stamp"
    $env:PYTHONPATH = "$MgRoot\LEGION-Federation;$MgRoot\LEGION-Marketplace"; $env:LMF_NIM_KEY_FILE = (Resolve-Path $NimKeyFile).Path
    Push-Location "$MgRoot\LEGION-Marketplace"
    & "$MgRoot\.venv-genesis\Scripts\python.exe" -m legion_marketplace.e2e --core "$MgRoot\LEGION-Core-pinned" --out $out --arm A2
    Pop-Location
    python -B -m deal_rescue.dossier --run $out --mode FRESH_SYNTHETIC --run-id "fresh-$stamp" --paypal-state "$PaypalHistoric\PAUSED_STATE.json" --paypal-mission "$PaypalHistoric\MISSION_AUTHORIZED.json" --out (Join-Path $Evidence "dossier\fresh-$stamp")
}
Get-ChildItem (Join-Path $Evidence "runs") -Directory | ForEach-Object { Write-Host "run available: $($_.Name)" }
python -B -m deal_rescue.audit --evidence $Evidence
python -B -m deal_rescue.server --evidence $Evidence --port $Port
