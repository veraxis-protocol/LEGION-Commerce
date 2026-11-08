#!/bin/bash
# WO-COM-E2E-003: run the five fixed tasks under one configuration. Usage: run_suite_e2e003.sh <label> [model]
# Budgets identical to E2E-002: 420 s wall clock per task in the driver, 80 backend calls in the checker.
# T5 carries the task contract (completion gate); T1-T4 run without one. MODEL unset = the backend default (nim.MODEL).
cd /c/Users/Administrator/Documents/ChatGPT/Hackathons/LEGION-Commerce-Q002/e2e001
E=../e2e003_evidence; L=$1; export MODEL=$2
for T in T1 T2 T3 T4 T5; do
  S=${L}_${T}
  if [ $T = T5 ]; then export CONTRACT_FILE=$E/tasks/T5_contract.json; else unset CONTRACT_FILE; fi
  node tools/chat.mjs $E/$S $S nim $E/tasks/${T}_prompt.txt - 420 > $E/${S}.driver.log 2>&1
  python tools/check_task.py $E/tasks/TASKS.json $T ai_turns/$S.jsonl $E/$S/CHAT_RESULT.json $E/$S/CHECK.json | tee -a $E/${L}_SUITE.log
done
echo "SUITE_DONE $L" | tee -a $E/${L}_SUITE.log
