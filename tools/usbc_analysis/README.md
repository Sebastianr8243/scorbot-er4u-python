# USBC.dll static analysis

How the findings in [docs/VENDOR_DLL_PROTOCOL.md](../../docs/VENDOR_DLL_PROTOCOL.md) were produced, so they can be checked or repeated on another build (for example the lab's own `USBC.dll`).

This reads the DLL as a file. It never loads or runs it, opens no USB device and cannot move the arm.

## Rules

- Keep the DLL, the Ghidra project and the decompiled dump **outside this repository**. They are Intelitek's code; the repository is public.
- Only the two files here are ours: `ExportDecomp.java` and `query.py`.
- Anything learned this way is "from disassembly, unverified" until a USB capture agrees.

## Steps

1. Install Ghidra and a JDK 21 anywhere (no admin rights needed; both run from an unpacked zip). Set `JAVA_HOME` to the JDK.
2. Make a work folder outside the repo with the DLL in it. Record the DLL's size, date and SHA-256 first.
3. Analyse and dump every function's decompiled C to one file:

    ```powershell
    $env:JAVA_HOME = "C:\path\to\jdk-21"
    $work = "C:\path\to\work"
    & "C:\path\to\ghidra\support\analyzeHeadless.bat" "$work\proj" usbc `
        -import "$work\USBC.dll" -overwrite `
        -scriptPath "<repo>\tools\usbc_analysis" `
        -postScript ExportDecomp.java "$work\usbc.c"
    ```

    About one minute. The log should end with `ExportDecomp: N ok, 0 failed`.

4. Query the dump (standard library only, any Python 3.10+):

    ```powershell
    python tools\usbc_analysis\query.py $work\usbc.c show Stop Control
    python tools\usbc_analysis\query.py $work\usbc.c callers WriteFile
    python tools\usbc_analysis\query.py $work\usbc.c grep "s_Clear_communication_buffer"
    ```

## Where to start reading

Addresses differ between builds, so find functions by what they contain:

| Looking for | Search |
|---|---|
| Communication thread (64-byte write and read) | `grep "ReadFile\("`, the function that also calls `CreateFileA` and `WriteFile` |
| Command-letter names | `grep "s_Clear_communication_buffer"` |
| Count decoding | `grep "0x7fffff;"` |
| Message builders | `callers` of the function the command-name table's neighbours call to zero a 64-byte message; each builder sets `local_40` to one command byte |
| Exported entry points | `show Stop`, `show Control`, `show Home`, `show MoveManual`, `show Initialization` |
