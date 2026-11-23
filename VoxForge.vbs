Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = dir
venvPy = dir & "\.venv\Scripts\pythonw.exe"
If fso.FileExists(venvPy) Then
    WshShell.Run """" & venvPy & """ """ & dir & "\main.py""", 0, False
Else
    WshShell.Run "pythonw """ & dir & "\main.py""", 0, False
End If
