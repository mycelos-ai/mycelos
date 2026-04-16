#!/bin/bash
# scripts/build-frontend.sh — Build frontend and copy to Python package
set -euo pipefail

echo "Building Maicel frontend..."
cd frontend
npm ci --prefer-offline
npm run build
echo "Frontend built → frontend/out/"

# Copy to Python package location (rm first to avoid cp -r nesting on rebuild)
rm -rf ../src/maicel/frontend/out
mkdir -p ../src/maicel/frontend/
cp -r out ../src/maicel/frontend/out
echo "Copied to src/maicel/frontend/out/"
