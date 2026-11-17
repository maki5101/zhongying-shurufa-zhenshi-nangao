// 查鼠标在哪、左键是不是还按着（X11 / XWayland 下有效；纯 Wayland 连不上就一律返回 false）。
//
// 用途：Chromium/Electron 系应用（Chrome、微信、各种 Electron 客户端）鼠标点击时不向输入法上报
// 光标位置（方向键、打字、上屏都报，唯独点击不报），面板会画在上一次上报的老地方。点击引起的
// FocusIn/Reset 到达时鼠标左键通常还按着，这时把鼠标位置当光标位置喂给面板；应用之后真上报了
// 再以应用的为准。按键已经抬起的事件（应用自己发的 reset）不动，宁可留在老位置也别乱跳。
//
// 自己开一条 xcb 连接，不复用 fcitx5 的：独立、出错不影响别人。用 xcb 不用 Xlib——Xlib 连接断了
// 默认直接 exit()，会把整个 fcitx5 带走。
#ifndef SHUOHUAA_POINTER_X11_H
#define SHUOHUAA_POINTER_X11_H

#include <cstdlib>
#include <string>
#include <xcb/xcb.h>

struct PointerState {
    int x = 0, y = 0;
    bool button1 = false;
};

class X11Pointer {
public:
    X11Pointer() {
        int screenNum = 0;
        conn_ = xcb_connect(nullptr, &screenNum);
        if (!conn_ || xcb_connection_has_error(conn_)) {
            if (conn_) {
                xcb_disconnect(conn_);
            }
            conn_ = nullptr;
            return;
        }
        auto iter = xcb_setup_roots_iterator(xcb_get_setup(conn_));
        for (int i = 0; i < screenNum && iter.rem; ++i) {
            xcb_screen_next(&iter);
        }
        root_ = iter.data ? iter.data->root : 0;
    }
    ~X11Pointer() {
        if (conn_) {
            xcb_disconnect(conn_);
        }
    }
    X11Pointer(const X11Pointer &) = delete;
    X11Pointer &operator=(const X11Pointer &) = delete;

    /** Wayland 会话下一律不可用：面板位置由合成器（KWin）按应用上报的光标定，插件喂的全局坐标没意义，
     *  而且 XWayland 报的鼠标位置对 Wayland 原生窗口是过期的。 */
    static bool waylandSession() {
        const char *w = std::getenv("WAYLAND_DISPLAY");
        const char *t = std::getenv("XDG_SESSION_TYPE");
        return (w && *w) || (t && std::string(t) == "wayland");
    }

    bool available() const { return !waylandSession() && conn_ && root_ && !xcb_connection_has_error(conn_); }

    /** 根窗口（整个屏幕，多屏是拼起来的总尺寸）的大小 */
    bool screenSize(int &w, int &h) {
        if (!available()) {
            return false;
        }
        auto *reply = xcb_get_geometry_reply(conn_, xcb_get_geometry(conn_, root_), nullptr);
        if (!reply) {
            return false;
        }
        w = reply->width;
        h = reply->height;
        std::free(reply);
        return w > 0 && h > 0;
    }

    bool query(PointerState &out) {
        if (!available()) {
            return false;
        }
        auto cookie = xcb_query_pointer(conn_, root_);
        auto *reply = xcb_query_pointer_reply(conn_, cookie, nullptr);
        if (!reply) {
            return false;
        }
        out.x = reply->root_x;
        out.y = reply->root_y;
        out.button1 = (reply->mask & XCB_BUTTON_MASK_1) != 0;
        std::free(reply);
        return true;
    }

private:
    xcb_connection_t *conn_ = nullptr;
    xcb_window_t root_ = 0;
};

#endif
