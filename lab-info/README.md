# Lab info (copy and paste)

Visit A, lab PC. Run in PowerShell. Paste the output back to the agent.
Known so far: SCORBASE 5.6, controller software version 14. Full checklist: `docs/lab/VISIT_A_AGENT_CHECKLIST.md`.

## 1. Find SCORBASE and the DLL

```powershell
Get-ChildItem C:\ -Directory -Filter "*scorbase*" -ErrorAction SilentlyContinue | Select FullName
Get-ChildItem C:\ -Recurse -Filter USBC.dll -ErrorAction SilentlyContinue | Select FullName, Length, LastWriteTime
```

## 2. Header and library files (replace the folder)

```powershell
Get-ChildItem "<scorbase folder>" -Recurse -Include usbc.h,usbcdef.h,error.h,usbc.lib | Select FullName
```

## 3. Wireshark and USBPcap

```powershell
Test-Path "C:\Program Files\Wireshark\Wireshark.exe"
Get-Service USBPcap -ErrorAction SilentlyContinue
```

## 4. Is the repo on this PC

```powershell
Test-Path C:\Users\sebas\startup\openScorbot
```

## 5. Step 0 backups (replace `<bin>` with the SCORBASE BIN folder; arm idle, nothing connected)

```powershell
Copy-Item "<repo>\logs" "<stick>\logs_backup" -Recurse
Copy-Item "<bin>" C:\scratch\bin -Recurse
```
