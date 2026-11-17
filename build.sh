#!/bin/bash
# 编译。只编译不安装；安装要写系统目录，得你自己 sudo：
#   sudo cmake --install build，然后重启 fcitx5（脚本末尾会按会话打印正确的重启命令：
#   Wayland 下要让 KWin 重新拉起（关/开虚拟键盘），pkill 和 fcitx5 -rd 都不行；X11 下 fcitx5 -rd >/dev/null 2>&1）
#
#   build.sh         编译
#   build.sh --deb   编完打成 .deb（在 build-deb/），别的 Debian/Ubuntu 机器 sudo apt install ./xxx.deb 即可
set -eu
cd "$(dirname "$0")"
# 重启 fcitx5：Wayland（KDE）下 fcitx5 是 KWin 的启动器拉起的，只有它能拿到原生输入法协议，
# 手动 fcitx5 -rd 起的那个会退化成普通客户端（面板、焦点都不对）；杀掉它让启动器重开即可
restart_fcitx5_hint() {
    if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
        echo "  # Wayland：必须由 KWin 重新拉起——先关再开 系统设置 → 键盘 → 虚拟键盘 里的 Fcitx 5"
        echo "  # （或者：busctl --user set-property org.kde.KWin /VirtualKeyboard org.kde.kwin.VirtualKeyboard enabled b false; pkill -x fcitx5; sleep 1; 同样命令 …enabled b true）"
        echo "  # pkill 或 fcitx5 -rd 起的 fcitx5 拿不到原生协议，Rime 会整个失灵"
    else
        echo "  fcitx5 -rd >/dev/null 2>&1"
    fi
}

deb=0; dir=build
[ "${1:-}" = "--deb" ] && { deb=1; dir=build-deb; }   # 单独目录：build/ 里 sudo 装过后留下 root 的文件，cpack 写不了
if ! pkg-config --exists Fcitx5Core 2>/dev/null; then
    echo "缺 fcitx5 开发包，先装：sudo apt install fcitx5-modules-dev libfcitx5core-dev libfcitx5utils-dev libfcitx5config-dev libxcb1-dev cmake g++"
    exit 1
fi
cmake -B "$dir" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr >/dev/null
cmake --build "$dir" -j
echo
if [ "$deb" = 1 ]; then
    (cd "$dir" && cpack >/dev/null)
    echo "已打包：$(ls "$dir"/*.deb)"
    echo "装：sudo apt install ./$(ls "$dir"/*.deb | head -1)   然后重启 fcitx5："; restart_fcitx5_hint
    exit 0
fi
echo "编译完成。安装（需要 sudo）："
echo "  sudo cmake --install $(pwd)/build"
echo "然后重启 fcitx5："; restart_fcitx5_hint
