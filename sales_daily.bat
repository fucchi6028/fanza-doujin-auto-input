@echo off
rem FANZA売上（卸金額）自動集計 - Windowsタスクスケジューラから毎朝8:30に実行する
rem 集計・Discord通知のあと、スマホ用の共有ページ（GitHub Pages）も更新する
rem 注意: このファイルは必ず CP932(Shift-JIS) + CRLF で保存すること。
rem       UTF-8 や LF のままだと cmd.exe が行を読み違えて python が起動しない。
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "%~dp0logs" mkdir "%~dp0logs"
rem 起動したこと自体の記録（python が落ちても残るので無音の失敗に気づける）
rem リダイレクトは行頭に置く。末尾に置くと %RC% の数字と >> がくっついて壊れる。
>>"%~dp0logs\sales_task.log" echo [%date% %time%] start PY="%PY%" args=%*
"%PY%" sales_daily.py %*
set "RC=%errorlevel%"
rem 一部未取得（RC=1）でも、取れているぶんは共有ページに反映しておく
"%PY%" sales_publish.py
set "RC2=%errorlevel%"
>>"%~dp0logs\sales_task.log" echo [%date% %time%] end daily=%RC% publish=%RC2%
exit /b %RC%
