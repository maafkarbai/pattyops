Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
python = root & "\.venv\Scripts\pythonw.exe"
If Not fs.FileExists(python) Then
    MsgBox "This source folder has not been prepared. Ask your installer for PattyOps-Kitchen-Setup.exe.", 48, "PattyOps setup needed"
Else
    shell.CurrentDirectory = root
    shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & root & "\kitchen_app.py" & Chr(34), 0, False
End If
