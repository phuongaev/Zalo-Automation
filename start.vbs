Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
objShell.Run """" & objShell.CurrentDirectory & "\.venv\Scripts\pythonw.exe"" run.py", 0, False
