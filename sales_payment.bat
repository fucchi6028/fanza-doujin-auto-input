@echo off
rem FANZA実振込額の取得 - Windowsタスクスケジューラから毎日16:00に実行する
rem 振込日（翌月20日・休日なら次の平日）以外は sales_payment.py がすぐ終了する
rem 注意: このファイルは必ず CP932(Shift-JIS) + CRLF で保存すること。
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "%~dp0logs" mkdir "%~dp0logs"
"%PY%" sales_payment.py %*
set "RC=%errorlevel%"
rem 振込を取得した日だけ記録を残す（対象外の日は RC=0 で静かに終わる）
>>"%~dp0logs\sales_task.log" echo [%date% %time%] payment=%RC%
exit /b %RC%
