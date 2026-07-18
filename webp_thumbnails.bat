@echo off
setlocal enableextensions

rem  Morrowind PNG -> WebP batch converter (IrfanView engine, parallel).

rem ===================== CONFIG =====================
set "IRFANVIEW=C:\Program Files\IrfanView\i_view64.exe"

set "SOURCE="
set "OUTPUT_DIRECTORY=%~dp0output webp"

set "RENDERS_DIRECTORY=renders"
set "RENDERS_SIZE=1024"
set "THUMBNAILS_DIRECTORY=thumbnails"
set "THUMBNAILS_SIZE=256"

set "WEBP_LOSSLESS=0"
set "WEBP_QUALITY=75"
set "WEBP_METHOD=4"
set "WEBP_PASSES=1"

rem Parallel workers (blank = one per CPU core).
set "WEBP_WORKERS="
rem ==================================================


rem --- Console colours (ANSI, same palette as colors.py) -------
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "C_TITLE=%ESC%[38;2;202;165;96m"
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

if not defined SOURCE goto :no_source_given
set "SOURCE=%SOURCE:"=%"
if "%SOURCE:~-1%"=="\" set "SOURCE=%SOURCE:~0,-1%"
if not defined SOURCE   goto :no_source_given
if not exist "%SOURCE%" goto :no_source_exist

set "WORKERS_ARG="
if defined WEBP_WORKERS set "WORKERS_ARG=-workers %WEBP_WORKERS%"

echo %C_INFO%Source  :%C_RESET% %SOURCE%
echo %C_INFO%Output  :%C_RESET% %OUTPUT_DIRECTORY%
echo %C_INFO%Profiles:%C_RESET% %RENDERS_DIRECTORY% (%RENDERS_SIZE%px), %THUMBNAILS_DIRECTORY% (%THUMBNAILS_SIZE%px)
echo %C_INFO%WebP    :%C_RESET% quality %WEBP_QUALITY%, method %WEBP_METHOD%, passes %WEBP_PASSES%, lossless %WEBP_LOSSLESS%
echo.
echo %C_HEAD%Converting...%C_RESET%
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0run_webp_parallel.ps1" -irfanview "%IRFANVIEW%" -input_dir "%SOURCE%" -output_dir "%OUTPUT_DIRECTORY%" -renders_name "%RENDERS_DIRECTORY%" -renders_size %RENDERS_SIZE% -thumbnails_name "%THUMBNAILS_DIRECTORY%" -thumbnails_size %THUMBNAILS_SIZE% -quality %WEBP_QUALITY% -method %WEBP_METHOD% -passes %WEBP_PASSES% -lossless %WEBP_LOSSLESS% %WORKERS_ARG%
if errorlevel 1 goto :failed

echo.
echo %C_OK%Done.%C_RESET%
pause
exit /b 0


:failed
echo.
echo %C_ERR%Finished with errors (some files may be missing).%C_RESET%
pause
exit /b 1

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
