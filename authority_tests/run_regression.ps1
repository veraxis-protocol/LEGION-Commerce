$ErrorActionPreference = "Continue"
Set-Location C:\Users\Administrator\Documents\ChatGPT\Hackathons\LEGION-Commerce-Q002
$py = ".\.venv\Scripts\python.exe"; $E = "authority_tests\evidence\regression"
"START $(git rev-parse HEAD) $(Get-Date -Format o)"
& $py -B -m unittest discover -s tests -t . -v *> "$E\A04_unittest.log"; "A04 exit $LASTEXITCODE"
& $py -B -m unittest bridge_tests.test_conformance -v *> "$E\A03_v1_conformance.log"; "A03 v1 exit $LASTEXITCODE"
& $py -B tools\qualify_commerce.py --arch BCh *> "$E\A03_qualify_BCh.log"; "qualify BCh exit $LASTEXITCODE"
Start-Sleep 45; while (Get-Process lake,lean -ErrorAction SilentlyContinue) { Start-Sleep 30 }
& $py -B latency004\bench.py "$E\bench_dedicated_BCh_b2b" 1000 10 "BCh" "repeat,fresh" *> "$E\bench_dedicated_BCh_b2b.log"; "bench exit $LASTEXITCODE"
Start-Sleep 45; $env:PACE_MS="20"; & $py -B latency004\bench.py "$E\bench_dedicated_BCh_paced20" 1000 10 "BCh" "repeat,fresh" *> "$E\bench_dedicated_BCh_paced20.log"; "paced exit $LASTEXITCODE"
"DONE $(Get-Date -Format o)"
