Get-ChildItem -Path C:\ -Recurse -Include ER4Ax*.ini,ROB_4u.INI,ER4CONF.INI,USBC.INI,USBC.dll -ErrorAction SilentlyContinue | Select-Object FullName, Length, LastWriteTime
