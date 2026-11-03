#!/usr/bin/env bash
# Builds out/depth3d-glasses.zip for Decky → Developer → "Install plugin from ZIP".
set -euo pipefail
cd "$(dirname "$0")/.."
pnpm run build
rm -rf out && mkdir -p out/depth3d-glasses/py_modules
cp -r dist main.py plugin.json package.json LICENSE README.md out/depth3d-glasses/
cp -r py_modules/depth3d out/depth3d-glasses/py_modules/
rm -f out/depth3d-glasses/dist/*.map
find out -name __pycache__ -prune -exec rm -rf {} +
(cd out && zip -qr depth3d-glasses.zip depth3d-glasses)
echo "out/depth3d-glasses.zip"
