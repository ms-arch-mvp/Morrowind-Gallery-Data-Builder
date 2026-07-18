@echo off
setlocal enableextensions

rem  Morrowind PNG -> WebP batch converter (IrfanView engine).

rem ===================== CONFIG =====================
set "IRFANVIEW=C:\Program Files\IrfanView\i_view64.exe"

set "SOURCE="
set "OUTPUT_DIRECTORY=%~dp0output_webp"

set "RENDERS_DIRECTORY=renders"
set "RENDERS_SIZE=1024"
set "THUMBNAILS_DIRECTORY=thumbnails"
set "THUMBNAILS_SIZE=256"

set "WEBP_LOSSLESS=0"
set "WEBP_QUALITY=75"
set "WEBP_METHOD=4"
set "WEBP_PASSES=1"
rem ==================================================


rem --- Console colours (ANSI, same palette as colors.py) -------
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "C_TITLE=%ESC%[96m"
set "C_HEAD=%ESC%[95m"
set "C_INFO=%ESC%[94m"
set "C_OK=%ESC%[92m"
set "C_WARN=%ESC%[93m"
set "C_ERR=%ESC%[91m"
set "C_DIM=%ESC%[90m"
set "C_RESET=%ESC%[0m"

echo %C_TITLE%========================================%C_RESET%
echo %C_TITLE%  Morrowind PNG to WebP Thumbnails%C_RESET%
echo %C_TITLE%========================================%C_RESET%
echo.

if not exist "%IRFANVIEW%" set "IRFANVIEW=C:\Program Files (x86)\IrfanView\i_view32.exe"
if not exist "%IRFANVIEW%" goto :no_irfanview

rem --- Resolve the input folder: dragged-on argument > CONFIG > prompt ---
if not "%~1"=="" set "SOURCE=%~1"
if not defined SOURCE (
    echo Enter the input folder that contains the PNGs.
    echo.
    set /p "SOURCE=Input folder: "
)

rem Bail out first if nothing was entered (avoids expanding an empty var below).
if not defined SOURCE goto :no_source_given

rem Strip surrounding quotes (drag-and-drop adds them).
set "SOURCE=%SOURCE:"=%"
rem Strip a trailing backslash so relative paths are computed correctly.
if "%SOURCE:~-1%"=="\" set "SOURCE=%SOURCE:~0,-1%"

if not defined SOURCE   goto :no_source_given
if not exist "%SOURCE%" goto :no_source_exist

rem --- Write a private IrfanView ini with the WebP settings -----
set "INI_FOLDER=%TEMP%\webp_thumbnails_irfanview"
if not exist "%INI_FOLDER%" mkdir "%INI_FOLDER%"
set "INI_FILE=%INI_FOLDER%\i_view64.ini"
> "%INI_FILE%"  echo [WEBP]
>>"%INI_FILE%"  echo SaveOption=%WEBP_LOSSLESS%
>>"%INI_FILE%"  echo SaveQuality=%WEBP_QUALITY%
>>"%INI_FILE%"  echo Method=%WEBP_METHOD%
>>"%INI_FILE%"  echo Passes=%WEBP_PASSES%
>>"%INI_FILE%"  echo SavePreset=0
>>"%INI_FILE%"  echo SaveFilter=0
>>"%INI_FILE%"  echo SaveFilterStrength=60
>>"%INI_FILE%"  echo SaveSharpness=0
>>"%INI_FILE%"  echo SaveSharpnessValue=0

rem Length of SOURCE path, used to derive each folder's relative subpath.
call :strlen SOURCE_LENGTH "%SOURCE%"

echo.
echo %C_INFO%Source  :%C_RESET% %SOURCE%
echo %C_INFO%Output  :%C_RESET% %OUTPUT_DIRECTORY%
echo %C_INFO%Profiles:%C_RESET% %RENDERS_DIRECTORY% (%RENDERS_SIZE%px), %THUMBNAILS_DIRECTORY% (%THUMBNAILS_SIZE%px)
echo %C_INFO%WebP    :%C_RESET% quality %WEBP_QUALITY%, method %WEBP_METHOD%, passes %WEBP_PASSES%, lossless %WEBP_LOSSLESS%
echo.
echo %C_HEAD%Converting...%C_RESET%
echo.

if not exist "%OUTPUT_DIRECTORY%" mkdir "%OUTPUT_DIRECTORY%"

rem Process the source root, then every subfolder.
call :convert_folder "%SOURCE%"
for /d /r "%SOURCE%" %%D in (*) do call :convert_folder "%%D"

echo.
echo %C_OK%Done.%C_RESET%
pause
exit /b 0


rem ------------------------------------------------------------
rem  :convert_folder <folder>   -- convert all PNGs in one folder
rem ------------------------------------------------------------
:convert_folder
set "FOLDER=%~1"
if not exist "%FOLDER%\*.png" goto :eof

setlocal enabledelayedexpansion
rem Relative subpath (empty for the root, else \meshes\...).
set "RELATIVE_PATH=!FOLDER:~%SOURCE_LENGTH%!"
echo %C_DIM%  .!RELATIVE_PATH!%C_RESET%

rem ---------------- PROFILES ----------------
rem  One :convert_profile call per output profile.
rem  Args: <sourceFolder> <relativeSubpath> <destinationBase> <sizePx>
call :convert_profile "!FOLDER!" "!RELATIVE_PATH!" "%OUTPUT_DIRECTORY%\%RENDERS_DIRECTORY%"    %RENDERS_SIZE%
call :convert_profile "!FOLDER!" "!RELATIVE_PATH!" "%OUTPUT_DIRECTORY%\%THUMBNAILS_DIRECTORY%" %THUMBNAILS_SIZE%
rem ------------------------------------------

endlocal
goto :eof


rem ------------------------------------------------------------
rem  :convert_profile <sourceFolder> <relativeSubpath> <destinationBase> <sizePx>
rem ------------------------------------------------------------
:convert_profile
setlocal enabledelayedexpansion
set "SOURCE_FOLDER=%~1"
set "RELATIVE_PATH=%~2"
set "DESTINATION=%~3"
set "SIZE=%~4"
if not exist "%DESTINATION%!RELATIVE_PATH!" mkdir "%DESTINATION%!RELATIVE_PATH!"
"%IRFANVIEW%" "%SOURCE_FOLDER%\*.png" /resize=(%SIZE%,%SIZE%) /aspectratio /resample /ini="%INI_FOLDER%" /convert="%DESTINATION%!RELATIVE_PATH!\*.webp"
endlocal
goto :eof


rem ------------------------------------------------------------
rem  :strlen <resultVar> <string>
rem ------------------------------------------------------------
:strlen
setlocal enabledelayedexpansion
set "s=%~2"
set "n=0"
:strlen_loop
if defined s (
    set "s=!s:~1!"
    set /a n+=1
    goto :strlen_loop
)
endlocal & set "%~1=%n%"
goto :eof


rem ------------------------------------------------------------
:no_irfanview
echo %C_ERR%ERROR: IrfanView not found at "%IRFANVIEW%".%C_RESET%
pause
exit /b 1

:no_source_given
echo %C_ERR%ERROR: No input folder provided.%C_RESET%
pause
exit /b 1

:no_source_exist
echo %C_ERR%ERROR: Input folder does not exist:%C_RESET%
echo   %SOURCE%
pause
exit /b 1
