# 中英输入法真是难搞（zhongying-shurufa-zhenshi-nangao）

[English](README.md) · MIT 协议

一个很小的 fcitx5 插件：用 Rime 时，切进输入框、在框里点一下、按方向键 / Home / End / 退格这类
移动光标的键、或按 Shift/Caps 切换了中英，光标旁闪 1.5 秒 `◆ 中文` 或 `◇ 英文`——不用先按一下 Shift 试。
打字过程中（有预编辑）不闪；不是 Rime 时什么都不做；应用上报新光标位置时提示跟过去。

每种情况该是什么样、哪些应用不跟光标、出问题怎么抓日志：
[说话啊仓库里的验收清单](https://github.com/maki5101/shuohuaa/blob/main/docs/cursor-hint-checklist.md)（两种提示共用一份）。

## 安装

```bash
sudo apt install fcitx5-modules-dev libfcitx5core-dev libfcitx5utils-dev libfcitx5config-dev extra-cmake-modules cmake g++
./build.sh
sudo cmake --install build
# Wayland（KDE）：必须由 KWin 重新拉起——在 系统设置 → 键盘 → 虚拟键盘 里把 Fcitx 5 关掉再打开
#   （pkill 或 fcitx5 -rd 起的进程拿不到原生输入法协议，Rime 会整个失灵）
# X11：fcitx5 -rd >/dev/null 2>&1   （-d 转后台，不然挂在终端上；重定向是日志会一直刷）
```

不想在目标机器上装编译环境：`./build.sh --deb` 打出 `build-deb/fcitx5-rimeindicator_*.deb`，
拷过去 `sudo apt install ./fcitx5-rimeindicator_*.deb`，然后同上重启 fcitx5。

## 没反应？

```bash
dbus-send --session --print-reply --dest=org.fcitx.Fcitx5 /controller org.fcitx.Fcitx.Controller1.SetLogRule string:"rimeindicator=5"
journalctl --user -f -o cat | grep rimeindicator      # Wayland/KDE：fcitx5 由 KWin 拉起，日志在 journal；X11 自己起的话看它的终端
```

每个事件一行（`FocusIn` / `Reset` / `导航键` / `切换键后 subMode 变了` / `CursorRectChanged`），
闪了有 `闪现 subMode=…`，没闪有 `跳过：原因`。没有事件行 = 应用没发事件；有 `闪现` 没看到 =
面板位置/主题的问题（多半是应用没上报光标位置）。

## 和语音输入法「说话啊」的关系

[说话啊（shuohuaa）](https://github.com/maki5101/shuohuaa) 在光标旁显示录音状态时会让这个插件先别闪（通过 fcitx5 的
addon 调用机制，接口在 `rimeindicator_public.h`）。两边各自独立安装，装一个不需要另一个。
