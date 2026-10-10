@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === Сборка ParserGod для Windows ===

where py >nul 2>nul
if errorlevel 1 (
  echo Не найден Python. Установите Python 3.12 с python.org ^(галочка "Add python.exe to PATH"^) и запустите снова.
  pause & exit /b 1
)

if not exist .venv (
  echo Создаю виртуальное окружение...
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements-build.txt || (echo Ошибка установки зависимостей & pause & exit /b 1)

for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
if exist build rd /s /q build
if exist dist rd /s /q dist

pyinstaller --noconfirm ParserGod.spec || (echo Ошибка PyInstaller & pause & exit /b 1)

echo Самопроверка сборки...
start "" /wait dist\ParserGod\ParserGod.exe --selftest
if errorlevel 1 (
  echo Самопроверка НЕ пройдена. Отчёт ниже:
  type "%LOCALAPPDATA%\ParserGod\selftest.txt"
  pause & exit /b 1
)
type "%LOCALAPPDATA%\ParserGod\selftest.txt"

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if defined ISCC (
  echo Собираю установщик...
  %ISCC% /Qp installer.iss || (echo Ошибка Inno Setup & pause & exit /b 1)
  echo.
  echo Готово: папка release\ — установщик ParserGod-Setup-*.exe
) else (
  echo Inno Setup не найден — делаю zip-архив вместо установщика.
  if not exist release mkdir release
  powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\ParserGod' -DestinationPath 'release\ParserGod-windows.zip'"
  echo.
  echo Готово: release\ParserGod-windows.zip
  echo Для красивого установщика поставьте Inno Setup 6: https://jrsoftware.org/isdl.php и запустите сборку ещё раз.
)
pause
