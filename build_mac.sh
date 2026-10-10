#!/bin/bash
# Сборка ParserGod.app и .dmg на macOS. Запуск: bash build_mac.sh
set -euo pipefail
cd "$(dirname "$0")"
echo "=== Сборка ParserGod для macOS ($(uname -m)) ==="

PY=${PYTHON:-python3}
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements-build.txt

find . -name __pycache__ -prune -exec rm -rf {} +
rm -rf build dist
pyinstaller --noconfirm ParserGod.spec

echo "Самопроверка сборки..."
./dist/ParserGod.app/Contents/MacOS/ParserGod --selftest

# Подпись (необязательно): export SIGN_ID="Developer ID Application: Имя (TEAMID)"
if [ -n "${SIGN_ID:-}" ]; then
  codesign --deep --force --options runtime --timestamp --sign "$SIGN_ID" dist/ParserGod.app
fi

VERSION=$(python -c "from app import brand; print(brand.APP_VERSION)")
ARCH=$(uname -m)
mkdir -p release
DMG="release/ParserGod-${VERSION}-mac-${ARCH}.dmg"
rm -f "$DMG"
STAGE=$(mktemp -d)
cp -R dist/ParserGod.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "ParserGod" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "Готово: $DMG"
