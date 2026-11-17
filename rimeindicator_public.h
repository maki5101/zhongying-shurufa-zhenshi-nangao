// rimeindicator 对其他插件开放的接口（fcitx5 的 addon 调用机制）。
// 语音插件显示录音状态期间调 suppress(true)，别让"中文/英文"的闪现盖掉状态。
#ifndef SHUOHUAA_RIMEINDICATOR_PUBLIC_H
#define SHUOHUAA_RIMEINDICATOR_PUBLIC_H

#include <fcitx/addoninstance.h>

FCITX_ADDON_DECLARE_FUNCTION(RimeIndicator, suppress, void(bool));

#endif
