' Launch sales_payment.bat with no console window (for Task Scheduler)
Dim sh, dir
Set sh = CreateObject("WScript.Shell")
dir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
sh.CurrentDirectory = dir
sh.Run """" & dir & "sales_payment.bat""", 0, False
