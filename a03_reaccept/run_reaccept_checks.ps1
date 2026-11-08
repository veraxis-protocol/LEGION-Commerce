# A03 re-acceptance (owner decision 2026-11-08: conditional GO). Re-runs the contract checks on the current HEAD
# before an UNSIGNED v5 review request is emitted. Never signs, never uses PayPal credentials or network.
$ErrorActionPreference = "Continue"
Set-Location C:\Users\Administrator\Documents\ChatGPT\Hackathons\LEGION-Commerce-Q002
$py = ".\.venv\Scripts\python.exe"; $E = "a03_reaccept\evidence"
New-Item -ItemType Directory -Force $E | Out-Null
"START $(git rev-parse HEAD) dirty=$((git status --porcelain --untracked-files=no | Measure-Object).Count) $(Get-Date -Format o)"
& $py -B -m unittest discover -s tests -t . -v *> "$E\A04_unittest.log"; "A04 exit $LASTEXITCODE"
& $py -B -m unittest bridge_tests.test_conformance -v *> "$E\A03_v1_conformance.log"; "A03 v1 exit $LASTEXITCODE"
& $py -B tools\qualify_commerce.py --arch BCh *> "$E\A03_qualify_BCh.log"; "qualify BCh exit $LASTEXITCODE"
& $py -B authority_tests\run_adversarial.py "$E\adversarial" *> "$E\adversarial.log"; "adversarial exit $LASTEXITCODE"
& $py -B -m commerce_demo.formal001_negatives "$E\FORMAL001_NEGATIVES.json" *> "$E\formal001_negatives.log"; "formal001 exit $LASTEXITCODE"
"DONE $(git rev-parse HEAD) dirty=$((git status --porcelain --untracked-files=no | Measure-Object).Count) $(Get-Date -Format o)"
