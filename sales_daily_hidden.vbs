' Launch sales_daily.bat with no console window (for Task Scheduler)
Dim sh, dir
Set sh = CreateObject("WScript.Shell")
dir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.CurrentDirectory = dir
sh.Run """" & dir & "sales_daily.bat""", 0, False
