@echo off
rem FANZA売上管理アプリ（アカウント別の売上ダッシュボード）
rem 注意: このファイルは必ず CP932(Shift-JIS) + CRLF で保存すること。
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=pythonw"
start "" "%PY%" sales_app.py
