# Manual launcher (Task Scheduler does this automatically at logon).
# Starts the console-free supervisor; a second copy exits immediately.
Start-Process -FilePath "C:\Users\asare\AppData\Local\Programs\Python\Python313\pythonw.exe" `
    -ArgumentList "`"$PSScriptRoot\supervisor.py`"" -WorkingDirectory $PSScriptRoot
