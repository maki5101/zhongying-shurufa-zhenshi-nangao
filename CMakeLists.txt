cmake_minimum_required(VERSION 3.16)
project(fcitx5-rimeindicator VERSION 0.3.1 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)   # fcitx5 >= 5.1.19 的头文件要 C++20（std::span、requires）
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ECM（extra-cmake-modules）只用来加 uninstall 目标，可有可无：系统升级时它被清掉过，别让它拦住编译
find_package(ECM QUIET NO_MODULE)
if(ECM_FOUND)
    set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})
    include(ECMUninstallTarget)
endif()
include(GNUInstallDirs)
find_package(Fcitx5Core REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(XCB REQUIRED IMPORTED_TARGET xcb)   # 查鼠标位置用（pointer_x11.h）

add_library(rimeindicator MODULE rimeindicator.cpp)
target_link_libraries(rimeindicator PRIVATE Fcitx5::Core PkgConfig::XCB)
target_include_directories(rimeindicator PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(rimeindicator PROPERTIES PREFIX "")
install(TARGETS rimeindicator DESTINATION "${FCITX_INSTALL_ADDONDIR}")
# 不走 fcitx5_translate_desktop_file：它依赖 gettext 的 msgfmt，没装时会静默不产出。直接拷。
configure_file(rimeindicator-addon.conf.in "${CMAKE_CURRENT_BINARY_DIR}/rimeindicator.conf" COPYONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/rimeindicator.conf" DESTINATION "${FCITX_INSTALL_PKGDATADIR}/addon")

# 打成 .deb（build.sh --deb）
set(CPACK_GENERATOR DEB)
set(CPACK_PACKAGE_NAME "fcitx5-rimeindicator")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_CONTACT "zhongying-shurufa-zhenshi-nangao")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "fcitx5 addon: flash whether Rime is in Chinese or Latin mode next to the cursor")
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
set(CPACK_DEBIAN_FILE_NAME DEB-DEFAULT)
set(CPACK_DEBIAN_PACKAGE_SECTION "utils")
include(CPack)
