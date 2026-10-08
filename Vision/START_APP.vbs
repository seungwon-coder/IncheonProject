Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
On Error Resume Next
shell.Run "pythonw.exe " & Chr(34) & folder & "\start_app.pyw" & Chr(34), 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "pyw.exe -3 " & Chr(34) & folder & "\start_app.pyw" & Chr(34), 0, False
    If Err.Number <> 0 Then
        MsgBox "Python not found. Install Python, then run INSTALL.bat.", 16, "PV5"
    End If
End If
