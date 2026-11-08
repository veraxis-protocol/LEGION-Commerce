#!/bin/bash
# WO-COM-E2E-002: run the five fixed tasks on NIM under one adapter configuration. Usage: run_suite.sh <label>
cd /c/Users/Administrator/Documents/ChatGPT/Hackathons/LEGION-Commerce-Q002/e2e001
E=../e2e002_evidence; L=$1
for T in T1 T2 T3 T4 T5; do
  S=${L}_${T}
  node tools/chat.mjs $E/$S $S nim $E/tasks/${T}_prompt.txt - 420 > $E/${S}.driver.log 2>&1
  python tools/check_task.py $E/tasks/TASKS.json $T ai_turns/$S.jsonl $E/$S/CHAT_RESULT.json $E/$S/CHECK.json | tee -a $E/${L}_SUITE.log
done
echo "SUITE_DONE $L" | tee -a $E/${L}_SUITE.log
