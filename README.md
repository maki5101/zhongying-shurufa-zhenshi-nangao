# fcitx5 Rime indicator — see Chinese / English mode at the cursor

[中文说明](README.zh.md) · MIT License

A tiny fcitx5 add-on for Rime users. When you focus a text field, click inside it, move the cursor
(arrows / Home / End / Backspace) or toggle with Shift / Caps, it flashes `◆ 中文` or `◇ 英文` next to the cursor
for 1.5 s — so you know which mode you are in before you type, without pressing Shift to find out.
It stays quiet while you are composing, does nothing when the input method is not Rime, and follows the cursor
when the application reports a new position.

## Install

```bash
sudo apt install fcitx5-modules-dev libfcitx5core-dev libfcitx5utils-dev libfcitx5config-dev extra-cmake-modules cmake g++
./build.sh
sudo cmake --install build
```

Then restart fcitx5. On KDE Wayland it must be restarted by KWin (System Settings → Keyboard → Virtual Keyboard:
turn Fcitx 5 off and on again); on X11 run `fcitx5 -rd >/dev/null 2>&1`.

`./build.sh --deb` builds `build-deb/fcitx5-rimeindicator_*.deb` for machines without a build environment.

## Troubleshooting

```bash
dbus-send --session --print-reply --dest=org.fcitx.Fcitx5 /controller org.fcitx.Fcitx.Controller1.SetLogRule string:"rimeindicator=5"
journalctl --user -f -o cat | grep rimeindicator
```

Every event logs one line; a flash logs `闪现 subMode=…`, a skipped one logs the reason. No event line means the
application did not send the event; a flash line without anything on screen usually means the application does not
report the cursor position.

## Relation to Shuohuaa

[Shuohuaa](https://github.com/maki5101/shuohuaa) (voice typing) asks this add-on to stay quiet while it shows its
recording status at the cursor, through fcitx5's add-on call mechanism (`rimeindicator_public.h`). The two are
installed independently; neither needs the other.
