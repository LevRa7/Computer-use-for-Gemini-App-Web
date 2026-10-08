' ==============================================================================
'  Antigravity Mesh - run a node script with no console window at all
'
'  WHY THIS FILE EXISTS
'  Task Scheduler starts a task inside the interactive session, and the task has
'  no console of its own. When the action is powershell.exe, Windows therefore
'  allocates a NEW console for it and only then applies "-WindowStyle Hidden":
'  the window is created, drawn and hidden again, which the user sees as a black
'  window that appears and disappears. The watchdog task fires every five minutes,
'  so that flash is the most noticeable thing this project does on a desktop.
'
'  wscript.exe is a GUI host: it never allocates a console. WshShell.Run with a
'  window style of 0 passes SW_HIDE in the child's STARTUPINFO, so cmd.exe (and
'  therefore PowerShell) is created hidden from the very first moment - nothing is
'  ever drawn. This is the same mechanism the logon launcher
'  (antigravity-agent.vbs) uses, which is why a node starts without a window.
'
'  USAGE - the two scheduled tasks use exactly this. The script path is relative
'  to the payload (the directory that holds core\, ops\ and install.ps1), which
'  this file finds from its own location:
'
'     wscript.exe run-hidden.vbs "ops\windows\agent-watchdog.ps1" -Quiet
'     wscript.exe run-hidden.vbs "ops\update.ps1" -Quiet
'
'  Output goes to <ConfigDir>\<script name>.out.log: a window that never appears
'  also never shows an error.
'
'  ASCII only, and no BOM: wscript.exe reads a .vbs with the ANSI code page.
' ==============================================================================
Option Explicit

Dim shell, fso, payload, scriptPath, logDir, logPath, command, index, extra, quote

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If WScript.Arguments.Count < 1 Then
    WScript.Quit 2
End If

' <payload>\ops\windows\run-hidden.vbs -> three levels up is <payload>
payload = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName)))
scriptPath = payload & "\" & WScript.Arguments(0)

extra = ""
For index = 1 To WScript.Arguments.Count - 1
    extra = extra & " " & WScript.Arguments(index)
Next

logDir = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.config\antigravity-mesh"
If Not fso.FolderExists(logDir) Then
    logDir = payload
End If
logPath = logDir & "\" & fso.GetFileName(scriptPath) & ".out.log"

quote = Chr(34)
command = "cmd.exe /c " & quote & quote & "powershell.exe" & quote _
        & " -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive -File " _
        & quote & scriptPath & quote & extra _
        & " >> " & quote & logPath & quote & " 2>&1" & quote

' 0 = the child window is never shown, False = do not wait for it
shell.Run command, 0, False
WScript.Quit 0
