#!/bin/bash
# deploy.sh — 采集数据 → 生成 dashboard → push 到 GitHub Pages
# 用法: ./deploy.sh [--collect] [--generate-only]
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
SCANNER_DIR="$DIR/../iv-scanner"
cd "$DIR"

# 1. 采集数据（如果传了 --collect 或没有参数）
if [[ "$1" == "--collect" ]] || [[ -z "$1" ]]; then
  echo "📡 Running Futu IV collection..."
  cd "$SCANNER_DIR"
  python3 run_daily.py || echo "⚠️ Collection had issues (exit $?), continuing with existing data"
  cd "$DIR"
fi

# 2. 生成 HTML
echo "🔨 Generating dashboard..."
python3 generate.py

# 3. Commit & Push
if [[ "$1" != "--generate-only" ]]; then
  echo "🚀 Deploying to GitHub Pages..."
  git add -A
  CHANGED=$(git diff --cached --name-only)
  if [ -n "$CHANGED" ]; then
    DATE=$(date +%Y-%m-%d)
    git commit -m "data: update $DATE"
    git push origin main
    echo "✅ Deployed!"
  else
    echo "ℹ️ No changes to deploy"
  fi
fi
