Option Explicit

' Inicio sin consola. Tambien admite --stop y --no-browser para los accesos.
Dim files, shell, root, python, command, argument, code, detail
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(root, ".venv-working\Scripts\python.exe")
If Not files.FileExists(python) Then python = files.BuildPath(root, ".venv\Scripts\python.exe")
If Not files.FileExists(python) Then
    MsgBox "No se encontro Python para Finanzas. Revisa la instalacion de la app.", vbExclamation, "Iniciar Finanzas"
    WScript.Quit 1
End If

shell.CurrentDirectory = root
command = Quote(python) & " -B " & Quote(files.BuildPath(root, "scripts\start_finance_app.py")) & " --detached"
For Each argument In WScript.Arguments
    If argument = "--stop" Or argument = "--no-browser" Then command = command & " " & argument
Next

On Error Resume Next
code = shell.Run(command, 0, True)
If Err.Number <> 0 Then
    detail = Err.Description
    On Error GoTo 0
    MsgBox "No se pudo abrir Finanzas." & vbCrLf & detail, vbExclamation, "Finanzas"
    WScript.Quit 1
End If
On Error GoTo 0
If code <> 0 Then
    MsgBox "Finanzas no pudo completar el inicio o el cierre." & vbCrLf & _
        "Codigo: " & code & vbCrLf & vbCrLf & _
        "Puedes revisar el registro en:" & vbCrLf & files.BuildPath(root, "data\logs\launcher.log"), vbExclamation, "Finanzas"
End If
WScript.Quit code

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
