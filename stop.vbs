Set objShell = CreateObject("WScript.Shell")
objShell.Run "taskkill /f /im pythonw.exe", 0, True
