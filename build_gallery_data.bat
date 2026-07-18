@echo off
setlocal enabledelayedexpansion

rem ===================== CONFIG =====================
set "input_directory="
set "output_directory=%~dp0output_json"
set "skip_small_plugins=1"
set "min_plugin_size_kb=1024"
rem ==================================================


rem --- Console colours (ANSI, same palette as colors.py) -------
for /f %%a in ('echo prompt $E ^| cmd') do set "esc=%%a"
set "c_title=%esc%[38;2;202;165;96m"
set "c_head=%esc%[95m"
set "c_info=%esc%[94m"
set "c_ok=%esc%[92m"
set "c_warn=%esc%[93m"
set "c_err=%esc%[91m"
set "c_dim=%esc%[90m"
set "c_reset=%esc%[0m"

echo %c_title%========================================%c_reset%
echo %c_title%  Morrowind Gallery Data Builder%c_reset%
echo %c_title%========================================%c_reset%
echo.

rem --- Resolve the input folder: dragged-on argument > CONFIG > prompt ---
if not "%~1"=="" set "input_directory=%~1"
if not defined input_directory (
    echo Enter the folder containing the .esp/.esm files.
    echo.
    set /p "input_directory=Input folder: "
)

rem Bail out first if nothing was entered (avoids expanding an empty var below).
if not defined input_directory goto :no_input_given
rem Strip surrounding quotes (drag-and-drop adds them).
set "input_directory=%input_directory:"=%"
rem Strip a trailing backslash.
if "%input_directory:~-1%"=="\" set "input_directory=%input_directory:~0,-1%"
if not defined input_directory     goto :no_input_given
if not exist "%input_directory%\"  goto :no_input_exist

if not exist "%output_directory%" mkdir "%output_directory%"

rem Byte threshold for the small-plugin skip.
set /a size_threshold=min_plugin_size_kb*1024

echo %c_info%Input  :%c_reset% %input_directory%
echo %c_info%Output :%c_reset% %output_directory%
echo.
echo %c_head%Processing...%c_reset%
echo.

set /a filecount=0
for %%F in ("!input_directory!\*.esp" "!input_directory!\*.esm") do (
    if exist "%%F" set /a filecount+=1
)

for %%F in ("!input_directory!\*.esp" "!input_directory!\*.esm") do (
    if exist "%%F" (
        set "size=%%~zF"
        set "skip=0"
        if "%skip_small_plugins%"=="1" (
            if !filecount! GTR 1 (
                if !size! LEQ !size_threshold! set "skip=1"
            )
        )
        if !skip! EQU 1 (
            echo %c_warn%Skipping ^(under %min_plugin_size_kb% KB^): %%~nxF%c_reset%
        ) else (
            echo %c_dim%Processing: %%~nxF%c_reset%
            if exist "%output_directory%\%%~nF.json" del "%output_directory%\%%~nF.json"
            "%~dp0tes3conv.exe" -o "%%F" "%output_directory%\%%~nF.json"
            if !errorlevel! NEQ 0 (
                echo %c_err%  tes3conv failed on %%~nxF%c_reset%
            ) else (
                if exist "%output_directory%\%%~nF_filtered.json" del "%output_directory%\%%~nF_filtered.json"
                python "%~dp0generate_filtered_json.py" "%output_directory%\%%~nF.json" "%output_directory%\%%~nF_filtered.json"
            )
        )
    )
)

echo.
echo %c_ok%Done.%c_reset%
pause
exit /b 0


:no_input_given
echo %c_err%ERROR: No input folder provided.%c_reset%
pause
exit /b 1

:no_input_exist
echo %c_err%ERROR: Input folder does not exist:%c_reset%
echo   %input_directory%
pause
exit /b 1
