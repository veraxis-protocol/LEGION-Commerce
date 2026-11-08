# Starts headless Microsoft Edge with a DevTools endpoint on 127.0.0.1:9333 and a throwaway profile (WO-COM-E2E-002 checks).
$profile = Join-Path $env:TEMP "e2e002-edge-cdp"
Start-Process -FilePath "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" -ArgumentList "--headless=new","--disable-gpu","--remote-debugging-address=127.0.0.1","--remote-debugging-port=9333","--user-data-dir=$profile","--window-size=1800,2400","--no-first-run","about:blank"
