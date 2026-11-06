$ErrorActionPreference = "Continue"
Set-Location C:\Users\Administrator\Documents\ChatGPT\Hackathons\LEGION-Commerce-Q002
$py = ".\.venv\Scripts\python.exe"; $E = "latency004\evidence\final"
New-Item -ItemType Directory -Force $E | Out-Null
"START timing $(Get-Date -Format o)"
& $py -B latency004\bench.py "$E\bench_backtoback" 1000 10 "A,B,C,BC,BCs" "repeat,fresh,fresh_cold" *> "$E\bench_backtoback.log"; "bench b2b exit $LASTEXITCODE $(Get-Date -Format o)"
$env:PACE_MS = "20"; & $py -B latency004\bench.py "$E\bench_paced20" 1000 10 "A,BC" "repeat,fresh" *> "$E\bench_paced20.log"; Remove-Item Env:PACE_MS; "bench paced exit $LASTEXITCODE $(Get-Date -Format o)"
foreach ($a in "A","B","C","BC") { & $py -B latency004\profile_arch.py "$E\wp2_profiles" $a repeat 1000 100 *> "$E\wp2_profile_$a.log"; "profile $a exit $LASTEXITCODE" }
& $py -B latency004\profile_arch.py "$E\wp2_profiles" BC fresh 1000 100 *> "$E\wp2_profile_BC_fresh.log"; "profile BC fresh exit $LASTEXITCODE"
& $py -B latency004\exp_overlap.py "$E\E6_OVERLAP.json" 10 100 *> "$E\E6_overlap.log"; "overlap exit $LASTEXITCODE"
& $py -B latency004\exp_worker_glue.py "$E\E2b_WORKER_GLUE.json" *> "$E\E2b_worker_glue.log"; "glue exit $LASTEXITCODE"
& $py -B latency004\exp_ecoqos.py "$E\E1b_ECOQOS.json" 6 *> "$E\E1b_ecoqos.log"; "ecoqos exit $LASTEXITCODE"
& $py -B latency004\exp_file_open.py "$E\E1c_FILE_FIRST_OPEN.json" *> "$E\E1c_file_open.log"; "fileopen exit $LASTEXITCODE"
"TIMING DONE $(Get-Date -Format o)"
& $py -B tools\qualify_commerce.py --arch BC *> "$E\wp4_qualify_BC.log"; "qualify exit $LASTEXITCODE"
& $py -B latency004\robustness.py "$E\wp4_robustness" 8 150 *> "$E\wp4_robustness.log"; "robustness exit $LASTEXITCODE"
& $py -B latency004\equivalence.py "$E\wp4_equivalence_replay" 40 *> "$E\wp4_equivalence_replay.log"; "replay exit $LASTEXITCODE"
"DONE $(Get-Date -Format o)"
