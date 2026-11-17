#!/bin/bash
# 打发布包到 dist/：fcitx5-rimeindicator_<版本>_amd64.deb + SHA256SUMS（版本号在 CMakeLists.txt 的 project()）
#
#   tools/package.sh              正式包：从最新提交构建，工作区有没提交的改动就拒绝
#   tools/package.sh --snapshot   试打包：按"现在 git add -A 会提交的样子"构建，文件名带 -snapshot
#
# 隔离：源码一律从 git 取（git archive），在临时目录里构建，没进 git 的东西到不了包里；
# 打完用 tools/privacy_scan.py 扫成品，有私人信息或密钥就中止、不留产物。
set -eu
cd "$(dirname "$0")/.."
SNAPSHOT=0; [ "${1:-}" = "--snapshot" ] && SNAPSHOT=1
if [ "$SNAPSHOT" = 0 ] && [ -n "$(git status --porcelain)" ]; then
    echo "工作区有没提交的改动，正式包只从提交构建。先提交，或者用 --snapshot 试打包："
    git status --short | head -20
    exit 1
fi
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
if [ "$SNAPSHOT" = 1 ]; then
    IDX="$STAGE/index"   # 临时索引：真正的暂存区不动
    GIT_INDEX_FILE="$IDX" git read-tree HEAD
    GIT_INDEX_FILE="$IDX" git add -A
    REF="$(GIT_INDEX_FILE="$IDX" git write-tree)"
else
    REF=HEAD
fi
SRC="$STAGE/src"; mkdir -p "$SRC"
git archive "$REF" | tar -x -C "$SRC"
VER="$(sed -nE 's/^project\([^)]*VERSION ([0-9.]+).*/\1/p' "$SRC/CMakeLists.txt")"
TAG="$VER"; [ "$SNAPSHOT" = 1 ] && TAG="$VER-snapshot"

OUT="$STAGE/out"; mkdir -p "$OUT"
"$SRC/build.sh" --deb >/dev/null 2>&1 || { echo "编译失败，单独跑 ./build.sh 看错误"; exit 1; }
cp "$SRC/build-deb/fcitx5-rimeindicator_${VER}_amd64.deb" "$OUT/fcitx5-rimeindicator_${TAG}_amd64.deb"

python3 tools/privacy_scan.py "$OUT" || { echo "成品里有私人信息或密钥（见上），没有输出到 dist/"; exit 1; }
(cd "$OUT" && sha256sum -- * > SHA256SUMS)
rm -rf dist; mkdir -p dist; cp -p "$OUT"/* dist/
echo "dist/（源码：$([ "$SNAPSHOT" = 1 ] && echo "工作区快照" || git rev-parse --short HEAD)）:"
ls -la dist | awk 'NR>3{printf "  %-48s %s\n", $NF, $5}'
