@echo off
setlocal enabledelayedexpansion

set /p "indir=Enter path to folder containing .esp/.esm files: "

if not exist "!indir!\" (
    echo Folder not found: !indir!
    pause
    exit /b 1
)

set "outdir=%~dp0output"
if not exist "%outdir%" mkdir "%outdir%"

set /a filecount=0
for %%F in ("!indir!\*.esp" "!indir!\*.esm") do (
    if exist "%%F" set /a filecount+=1
)

for %%F in ("!indir!\*.esp" "!indir!\*.esm") do (
    if exist "%%F" (
        set "size=%%~zF"
        set "skip=0"
        if !filecount! GTR 1 (
            if !size! LEQ 1048576 set "skip=1"
        )
        if !skip! EQU 1 (
            echo Skipping ^(under 1MB^): %%~nxF
        ) else (
            echo Processing: %%~nxF
            if exist "%outdir%\%%~nF.json" del "%outdir%\%%~nF.json"
            "%~dp0tes3conv.exe" -o "%%F" "%outdir%\%%~nF.json"
            if !errorlevel! NEQ 0 (
                echo   tes3conv failed on %%~nxF
            ) else (
                if exist "%outdir%\%%~nF_filtered.json" del "%outdir%\%%~nF_filtered.json"
                python "%~dp0generate_filtered_json.py" "%outdir%\%%~nF.json" "%outdir%\%%~nF_filtered.json"
            )
        )
    )
)

echo Done.
pause
