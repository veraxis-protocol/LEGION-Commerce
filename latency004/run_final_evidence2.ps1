$ErrorActionPreference = "Continue"
Set-Location C:\Users\Administrator\Documents\ChatGPT\Hackathons\LEGION-Commerce-Q002
$py = ".\.venv\Scripts\python.exe"; $E = "latency004\evidence\final"
function Quiet { while (Get-Process lake,lean -ErrorAction SilentlyContinue) { "waiting: foreign lake/lean build active $(Get-Date -Format o)"; Start-Sleep 30 } }
"START2 $(git rev-parse HEAD) $(Get-Date -Format o)"; Start-Sleep 60
Quiet; $env:PACE_MS = "20"; & $py -B latency004\bench.py "$E\bench_dedicated_BC_paced20" 2000 10 "BC" "repeat,fresh" *> "$E\bench_dedicated_BC_paced20.log"; "dedicated paced exit $LASTEXITCODE $(Get-Date -Format o)"
Start-Sleep 60; Quiet; & $py -B latency004\bench.py "$E\bench_paced20_rerun" 1000 10 "A,BC" "repeat,fresh" *> "$E\bench_paced20_rerun.log"; Remove-Item Env:PACE_MS; "paced rerun exit $LASTEXITCODE"
Start-Sleep 60; Quiet; & $py -B latency004\profile_paced.py "$E\wp1_paced_profiles" BC 20 1000 100 *> "$E\wp1_profile_paced_BC.log"; "profile paced exit $LASTEXITCODE"
"TIMING2 DONE $(Get-Date -Format o)"
& $py -B tools\qualify_commerce.py --arch BC *> "$E\wp4_qualify_BC.log"; "qualify exit $LASTEXITCODE"
& $py -B latency004\robustness.py "$E\wp4_robustness" 8 150 *> "$E\wp4_robustness.log"; "robustness exit $LASTEXITCODE"
& $py -B latency004\test_clock_skew.py "$E\WP4_CLOCK_SKEW.json" *> "$E\wp4_clock_skew.log"; "clockskew exit $LASTEXITCODE"
& $py -B latency004\equivalence.py "$E\wp4_equivalence_run" 40 "A,B,C,BC" *> "$E\wp4_equivalence_run.log"; "equivalence exit $LASTEXITCODE $(Get-Date -Format o)"
& $py -B latency004\equivalence.py "$E\wp4_equivalence_replay" 40 "A,BC" *> "$E\wp4_equivalence_replay.log"; "replay exit $LASTEXITCODE"
"DONE $(Get-Date -Format o)"
