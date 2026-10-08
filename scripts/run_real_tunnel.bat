@echo off
rem One click on Windows: like run_real.bat, and the web also gets a public https:// address
rem (Cloudflare quick tunnel) that phones on any network open. Needs tools\cloudflared.exe.
call "%~dp0run_real.bat" tunnel
