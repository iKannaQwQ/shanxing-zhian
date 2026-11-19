#!/usr/bin/env bash
# 构建发布包（源码包 / 本机完整包）
#
# 用法:
#   bash scripts/package_release.sh                  # 源码包，不含权重
#   bash scripts/package_release.sh --with-weights   # 额外包含本机 perception/weights/*.pt
#
# 输出: dist/shanxing-zhian-<version>-<date>[-with-weights].tar.gz
# 说明: 源码包只包含 git 跟踪的文件 + 已构建的 frontend/dist；
#       不含 .venv/.tools/data/weights（--with-weights 除外）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_WEIGHTS=0
case "${1:-}" in
  --with-weights) WITH_WEIGHTS=1 ;;
  "" ) ;;
  *) echo "未知参数: $1" >&2; exit 1 ;;
esac

VERSION="$(cat VERSION 2>/dev/null || echo 0.0.0)"
DATE="$(date +%Y%m%d-%H%M)"
NAME="shanxing-zhian-${VERSION}-${DATE}"
[ "$WITH_WEIGHTS" -eq 1 ] && NAME="${NAME}-with-weights"

if [ -n "$(git status --porcelain)" ]; then
  echo "工作区不干净。先提交，再打包（保证 MANIFEST 的 git_commit 可复现）：" >&2
  git status --short >&2
  exit 1
fi
if [ ! -f frontend/dist/index.html ]; then
  echo "缺少 frontend/dist。先运行:" >&2
  echo "  cd frontend && PATH=\"$ROOT/.tools/node/bin:\$PATH\" npm run build" >&2
  exit 1
fi

DIST="$ROOT/dist"
STAGE="$DIST/$NAME"
rm -rf "$STAGE" "$DIST/$NAME.tar.gz" "$DIST/$NAME.tar.gz.sha256"
mkdir -p "$STAGE"

echo "==> git archive"
git archive --format=tar HEAD | tar -x -C "$STAGE"

echo "==> 加入前端构建产物"
cp -R frontend/dist "$STAGE/frontend/dist"

if [ "$WITH_WEIGHTS" -eq 1 ]; then
  echo "==> 加入本机权重（仅内部使用，勿公开分发）"
  mkdir -p "$STAGE/perception/weights"
  cp perception/weights/*.pt "$STAGE/perception/weights/" 2>/dev/null || true
  WEIGHT_NOTE="已包含 perception/weights/*.pt（内部使用，勿公开分发）"
else
  WEIGHT_NOTE="不含权重；完整系统请把 .pt 放入 perception/weights/"
fi

cat > "$STAGE/MANIFEST.txt" <<EOF
project: shanxing-zhian
version: $VERSION
built_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
git_commit: $(git rev-parse HEAD)
git_branch: $(git branch --show-current)
weights: $WEIGHT_NOTE
quickstart: bash scripts/quickstart.sh --install --demo
EOF

echo "==> 打包 $NAME.tar.gz"
tar -czf "$DIST/$NAME.tar.gz" -C "$DIST" "$NAME"
shasum -a 256 "$DIST/$NAME.tar.gz" > "$DIST/$NAME.tar.gz.sha256"

echo ""
echo "完成:"
ls -lh "$DIST/$NAME.tar.gz" "$DIST/$NAME.tar.gz.sha256"
echo "校验: shasum -a 256 -c $DIST/$NAME.tar.gz.sha256"
