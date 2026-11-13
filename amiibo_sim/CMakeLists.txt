cmake_minimum_required(VERSION 3.20)
project(amiibo_sim LANGUAGES C CXX)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(LVGL_SOURCE_DIR "" CACHE PATH "Path to the Template's LVGL 9.5 source")
set(LV_BUILD_CONF_PATH "${CMAKE_CURRENT_SOURCE_DIR}/lv_conf.h" CACHE FILEPATH "" FORCE)
set(CONFIG_LV_BUILD_DEMOS OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG_INTERNAL OFF CACHE BOOL "" FORCE)
if(LVGL_SOURCE_DIR)
    add_subdirectory("${LVGL_SOURCE_DIR}" lvgl EXCLUDE_FROM_ALL)
    set(lvgl_license "${LVGL_SOURCE_DIR}/LICENCE.txt")
else()
    include(FetchContent)
    FetchContent_Declare(lvgl GIT_REPOSITORY https://github.com/lvgl/lvgl.git
                         GIT_TAG v9.5.0 GIT_SHALLOW TRUE)
    FetchContent_MakeAvailable(lvgl)
    set(lvgl_license "${lvgl_SOURCE_DIR}/LICENCE.txt")
endif()

add_executable(amiibo_sim lcd.cpp)
target_link_libraries(amiibo_sim PRIVATE lvgl::lvgl m)
target_compile_options(amiibo_sim PRIVATE -Wall -Wextra -Wpedantic)

add_executable(amiibo-zero main.c ntag215.c st25r3916.c)
target_compile_definitions(amiibo-zero PRIVATE _POSIX_C_SOURCE=200809L)
target_compile_options(amiibo-zero PRIVATE -Wall -Wextra -Wpedantic)

install(TARGETS amiibo_sim RUNTIME DESTINATION bin)
install(TARGETS amiibo-zero RUNTIME DESTINATION lib/amiibo_sim)
install(FILES make_uid_pool.py generate_uid_pool.py
    DESTINATION lib/amiibo_sim)
install(PROGRAMS cap-setup DESTINATION lib/amiibo_sim)
install(FILES amiibo_sim.desktop DESTINATION share/APPLaunch/applications)
install(FILES README.md LICENSE THIRD_PARTY_NOTICES.md "${lvgl_license}"
    DESTINATION share/doc/amiibo-sim)
