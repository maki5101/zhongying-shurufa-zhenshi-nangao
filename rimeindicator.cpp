// fcitx5-rimeindicator：Rime 用户切进输入框、在框里点一下、或按方向键/退格这类移动光标的键时，
// 在光标旁闪 1.5 秒当前是"中文"还是"英文"，免得每次都要先按一下 Shift 才知道。
//
// 和语音输入无关，可以单独编译安装（build.sh --only-rime）。
// 中英状态 Rime 不注册成状态区开关（实测只有全角/繁简/标点三个），而是走引擎的 subMode：
// 英文时返回 "Latin Mode" 的翻译（zh_CN 是"英文模式"），中文时返回方案名，未就绪返回 ⌛。
// 用 rime 自己的翻译域比对，不硬编码中文。
//
// 显示成 "◆ 中文" / "◇ 英文"（实心/空心，和说话啊的 ● 录音中 / ○ 识别中 一个路数；面板字体没有 emoji）。
// 按 Shift/Caps 切换中英后也闪一次新状态：Rime 自己吃掉这些键，我们在它处理完之后（defer 一拍）
// 看 subMode 变没变，变了才闪——Ctrl 这类被 Rime 当 noop 的键就不会每次松开都闪。
// "英文"有两种：Rime 自己的英文模式（subMode 是 "Latin Mode" 的翻译），和 fcitx5 切到了英文键盘布局
// （当前输入法是 keyboard-xx——用户按 Shift 切换的多半是这个）。两种都显示 ◇ 英文；切换输入法那一下也闪。
// 光标位置变了（应用上报 CursorRectChanged）就重新定位面板，提示跟着光标走。
// 应用从没报过位置（rect 0,0）又没法用鼠标兜底：放屏幕正中并加粗，别画到左上角。
// Chromium/Electron 系应用点击时不上报光标位置：点击引起的事件到达时左键还按着，就拿鼠标位置
// 当光标位置（pointer_x11.h），应用之后真上报了再以它为准。
//
// 排查：fcitx5 -rd --verbose rimeindicator=5 ，每个事件（进/点/键/切换/光标移动）和跳过的原因都有一行。

#include <libintl.h>
#include <memory>
#include <string>

#include <fcitx-utils/event.h>
#include <fcitx-utils/handlertable.h>
#include <fcitx-utils/key.h>
#include <fcitx-utils/keysym.h>
#include <fcitx-utils/trackableobject.h>
#include <fcitx-utils/log.h>
#include <fcitx/addonfactory.h>
#include <fcitx/addoninstance.h>
#include <fcitx/addonmanager.h>
#include <fcitx/event.h>
#include <fcitx/inputcontext.h>
#include <fcitx/inputmethodengine.h>
#include <fcitx/inputmethodentry.h>
#include <fcitx/inputpanel.h>
#include <fcitx/instance.h>
#include <fcitx/text.h>
#include <fcitx/userinterface.h>

#include "pointer_x11.h"
#include "rimeindicator_public.h"

namespace {

FCITX_DEFINE_LOG_CATEGORY(rimeindicator_log, "rimeindicator");
#define RI_DEBUG() FCITX_LOGC(rimeindicator_log, Debug)

// Rime 默认的中英切换键（ascii_composer/switch_key）：Shift_L inline_ascii、Shift_R commit_text、
// Caps_Lock clear、Control noop。都在松开时生效，所以我们看的是 release
bool isSwitchKey(fcitx::KeySym sym) {
    return sym == FcitxKey_Shift_L || sym == FcitxKey_Shift_R || sym == FcitxKey_Caps_Lock ||
           sym == FcitxKey_Control_L || sym == FcitxKey_Control_R;
}

class RimeIndicator : public fcitx::AddonInstance {
public:
    explicit RimeIndicator(fcitx::Instance *instance) : instance_(instance) {
        focusInWatcher_ = instance_->watchEvent(
            fcitx::EventType::InputContextFocusIn,
            fcitx::EventWatcherPhase::PostInputMethod,
            [this](fcitx::Event &event) {
                auto *ic = static_cast<fcitx::InputContextEvent &>(event).inputContext();
                RI_DEBUG() << "FocusIn " << ic->program() << " " << rectStr(ic);
                flash(ic, /*click=*/true);
            });
        // 在同一个输入框里再点一下不会有 FocusIn，但应用会让输入法 reset，借这个事件
        resetWatcher_ = instance_->watchEvent(
            fcitx::EventType::InputContextReset,
            fcitx::EventWatcherPhase::PostInputMethod,
            [this](fcitx::Event &event) {
                auto *ic = static_cast<fcitx::InputContextEvent &>(event).inputContext();
                RI_DEBUG() << "Reset " << ic->program() << " " << rectStr(ic);
                flash(ic, /*click=*/true);
            });
        switchWatcher_ = instance_->watchEvent(
            fcitx::EventType::InputContextSwitchInputMethod,
            fcitx::EventWatcherPhase::PostInputMethod,
            [this](fcitx::Event &event) {
                onSwitchInputMethod(static_cast<fcitx::InputContextEvent &>(event).inputContext());
            });
        // 应用上报了新的光标位置：提示还在显示就把面板挪过去
        cursorWatcher_ = instance_->watchEvent(
            fcitx::EventType::InputContextCursorRectChanged,
            fcitx::EventWatcherPhase::PostInputMethod,
            [this](fcitx::Event &event) {
                auto *ic = static_cast<fcitx::InputContextEvent &>(event).inputContext();
                RI_DEBUG() << "CursorRectChanged " << (settingRect_ ? "（我们设的）" : "（应用报的）") << rectStr(ic)
                           << (shownOn_.get() == ic ? "，面板跟过去" : "");
                if (shownOn_.get() == ic) {
                    ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
                }
            });
        // 移动光标的键（方向/Home/End/翻页/退格/Delete）：没被输入法吃掉的（不在拼字）才闪
        keyWatcher_ = instance_->watchEvent(
            fcitx::EventType::InputContextKeyEvent,
            fcitx::EventWatcherPhase::PostInputMethod,
            [this](fcitx::Event &event) {
                auto &keyEvent = static_cast<fcitx::KeyEvent &>(event);
                if (keyEvent.isRelease()) {
                    if (isSwitchKey(keyEvent.key().sym())) {
                        afterSwitchKey(keyEvent.inputContext());
                    }
                    return;
                }
                if (keyEvent.accepted()) {
                    return;
                }
                static const fcitx::KeySym navKeys[] = {
                    FcitxKey_Left, FcitxKey_Right, FcitxKey_Up, FcitxKey_Down,
                    FcitxKey_Home, FcitxKey_End, FcitxKey_Page_Up, FcitxKey_Page_Down,
                    FcitxKey_BackSpace, FcitxKey_Delete,
                    FcitxKey_KP_Left, FcitxKey_KP_Right, FcitxKey_KP_Up, FcitxKey_KP_Down,
                    FcitxKey_KP_Home, FcitxKey_KP_End, FcitxKey_KP_Page_Up, FcitxKey_KP_Page_Down,
                };
                const auto sym = keyEvent.key().sym();
                for (auto k : navKeys) {
                    if (sym == k) {
                        RI_DEBUG() << "导航键 " << keyEvent.key().toString();
                        flash(keyEvent.inputContext());
                        return;
                    }
                }
            });
    }

    // 输入法切换（Shift 切到英文键盘布局 / 切回 Rime）：闪新状态
    void onSwitchInputMethod(fcitx::InputContext *ic) {
        RI_DEBUG() << "切换输入法 -> " << instance_->inputMethod(ic);
        flash(ic);
    }

    /** 别的插件（语音状态）正占着光标旁的位置：期间不闪，也不去清它的字 */
    void suppress(bool on) {
        suppressed_ = on;
        if (on) {
            flashTimer_.reset();
        }
    }

private:
    std::string subMode(fcitx::InputContext *ic) {
        auto *engine = instance_->inputMethodEngine(ic);
        const auto *entry = instance_->inputMethodEntry(ic);
        return (engine && entry) ? engine->subMode(*entry, *ic) : std::string();
    }

    // 当前状态：返回 "zh" / "en" / ""（不认识的输入法或 Rime 未就绪）。
    // 顺便把用来比对变化的标识（输入法名 + subMode）写进 key。
    std::string currentMode(fcitx::InputContext *ic, std::string &key) {
        const std::string im = instance_->inputMethod(ic);
        if (im == "rime") {
            const std::string mode = subMode(ic);
            key = "rime:" + mode;
            if (mode.empty() || mode == "\xe2\x8c\x9b") {   // 空 / ⌛（rime 未就绪）
                return "";
            }
            return (mode == dgettext("fcitx5-rime", "Latin Mode")) ? "en" : "zh";
        }
        key = im;
        if (im.rfind("keyboard-", 0) == 0) {   // 英文/其他键盘布局：对用户来说就是"英文"
            return "en";
        }
        return "";
    }

    // 切换键松开：Rime 已经处理完（我们在 PostInputMethod），但它的 UI 更新可能还在后面，
    // 推迟一拍再看模式；和上次看到的不一样才闪
    void afterSwitchKey(fcitx::InputContext *ic) {
        auto ref = ic->watch();
        deferred_ = instance_->eventLoop().addDeferEvent([this, ref](fcitx::EventSource *) {
            auto *ic2 = ref.get();
            if (!ic2) {
                return true;
            }
            std::string key;
            currentMode(ic2, key);
            if (key != lastMode_) {
                RI_DEBUG() << "切换键后状态变了：" << lastMode_ << " -> " << key;
                flash(ic2);
            }
            return true;
        });
    }

    // click=true：这次是 FocusIn/Reset 这类多半由鼠标点击引起的事件，可以用鼠标位置兜底
    void flash(fcitx::InputContext *ic, bool click = false) {
        if (suppressed_) {
            RI_DEBUG() << "跳过：被别的插件（录音状态）压着";
            return;
        }
        if (!ic->hasFocus()) {
            RI_DEBUG() << "跳过：没有焦点";
            return;
        }
        // 正在拼字（有预编辑）就别打扰
        if (!ic->inputPanel().preedit().empty() || !ic->inputPanel().clientPreedit().empty()) {
            RI_DEBUG() << "跳过：正在拼字";
            return;
        }
        std::string key;
        const std::string mode = currentMode(ic, key);
        if (mode.empty()) {
            RI_DEBUG() << "跳过：不认识的状态（" << key << "）";
            return;
        }
        lastMode_ = key;
        // 注意不能对连发的 FocusIn+Reset 去重：Electron 每次点击都连发两个，中间 fcitx5 会把面板收掉，
        // 第二个事件不再画一次面板就没了（2026-09-15 实测回退过一次）
        if (click) {
            placeAtPointerIfClicking(ic);
        }
        // 应用从没报过位置（新开的输入框，Chrome 点击不报）、鼠标也兜不了底：放屏幕正中、加粗，别画到左上角
        bool centered = false;
        if (ic->cursorRect().left() == 0 && ic->cursorRect().top() == 0) {
            int sw = 0, sh = 0;
            if (pointer_.screenSize(sw, sh)) {
                settingRect_ = true;
                ic->setCursorRect(fcitx::Rect(sw / 2, sh / 2, sw / 2 + 1, sh / 2 + 20), 1.0);
                settingRect_ = false;
                centered = true;
            }
        }
        const bool latin = (mode == "en");
        RI_DEBUG() << "闪现 " << key << " -> " << (latin ? "英文" : "中文") << (centered ? "（位置未知，放屏幕正中）" : "")
                   << "  程序 " << ic->program();
        // 不打高亮标记，用普通文字色：高亮色（青苔主题是红色）留给说话啊的"录音中"，一眼认出在录音（2026-10-04）
        fcitx::Text text(latin ? "◇ 英文" : "◆ 中文");
        if (centered) {
            text = fcitx::Text(latin ? "◇  英  文" : "◆  中  文", fcitx::TextFormatFlag::Bold);
        }
        ic->inputPanel().setAuxUp(text);
        ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
        shownOn_ = ic->watch();
        auto ref = ic->watch();
        flashTimer_ = instance_->eventLoop().addTimeEvent(
            CLOCK_MONOTONIC, fcitx::now(CLOCK_MONOTONIC) + kFlashUs, 0,
            [this, ref](fcitx::EventSourceTime *, uint64_t) {
                auto *ic2 = ref.get();
                if (ic2 && !suppressed_) {   // 期间没被别人占用才清
                    ic2->inputPanel().setAuxUp(fcitx::Text());
                    ic2->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel);
                }
                shownOn_.unwatch();   // 定时器对象留着（一次性，已失效），下次闪现整个换新
                return true;
            });
    }

    // 左键还按着 = 这个事件是刚才那下点击引起的，鼠标就在光标要去的地方：把它喂给面板当光标位置。
    // 应用（Qt/GTK）随后真上报的话会覆盖掉，我们的 CursorRectChanged 处理再把面板挪到真位置。
    void placeAtPointerIfClicking(fcitx::InputContext *ic) {
        PointerState p;
        if (!pointer_.query(p)) {
            if (X11Pointer::waylandSession()) {
                RI_DEBUG() << "Wayland 会话：面板位置由合成器定，不用鼠标兜底";
            }
            return;
        }
        if (!p.button1) {
            RI_DEBUG() << "左键没按着，不用鼠标位置（应用自己发的事件）";
            return;
        }
        RI_DEBUG() << "左键按着，面板放到鼠标处 (" << p.x << "," << p.y << ")，之前 " << rectStr(ic);
        settingRect_ = true;
        ic->setCursorRect(fcitx::Rect(p.x, p.y, p.x + 1, p.y + 20), 1.0);
        settingRect_ = false;
    }

    static constexpr uint64_t kFlashUs = 1500 * 1000;   // 提示停留 1.5 秒
    X11Pointer pointer_;
    bool settingRect_ = false;   // 正在由我们自己 setCursorRect（CursorRectChanged 回调里区分"我们设的/应用报的"）

    static std::string rectStr(fcitx::InputContext *ic) {
        const auto &r = ic->cursorRect();
        return "rect(" + std::to_string(r.left()) + "," + std::to_string(r.top()) + ")x" +
               std::to_string(ic->scaleFactor());
    }

    fcitx::Instance *instance_;
    bool suppressed_ = false;
    std::string lastMode_;                                // 上次闪现/检查时的 subMode，切换键后比对用
    fcitx::TrackableObjectReference<fcitx::InputContext> shownOn_;   // 提示正显示在哪个 IC 上
    std::unique_ptr<fcitx::EventSourceTime> flashTimer_;
    std::unique_ptr<fcitx::EventSource> deferred_;
    std::unique_ptr<fcitx::HandlerTableEntry<fcitx::EventHandler>> focusInWatcher_;
    std::unique_ptr<fcitx::HandlerTableEntry<fcitx::EventHandler>> resetWatcher_;
    std::unique_ptr<fcitx::HandlerTableEntry<fcitx::EventHandler>> keyWatcher_;
    std::unique_ptr<fcitx::HandlerTableEntry<fcitx::EventHandler>> cursorWatcher_;
    std::unique_ptr<fcitx::HandlerTableEntry<fcitx::EventHandler>> switchWatcher_;

    FCITX_ADDON_EXPORT_FUNCTION(RimeIndicator, suppress);
};

class RimeIndicatorFactory : public fcitx::AddonFactory {
public:
    fcitx::AddonInstance *create(fcitx::AddonManager *manager) override {
        return new RimeIndicator(manager->instance());
    }
};

} // namespace

FCITX_ADDON_FACTORY(RimeIndicatorFactory);
