#define main mimo_device_main
#include "../lcd.cpp"
#undef main
#include <cassert>
#include <fstream>

namespace {
std::vector<uint8_t> pixels(320 * 170 * 2);
void flush(lv_display_t *display, const lv_area_t *area, uint8_t *data) {
    for (int y = area->y1; y <= area->y2; ++y) {
        const int width = area->x2 - area->x1 + 1;
        std::memcpy(pixels.data() + (y * 320 + area->x1) * 2, data, width * 2);
        data += width * 2;
    }
    lv_display_flush_ready(display);
}
void snapshot(App &a, const char *name) {
    draw(a);
    lv_refr_now(nullptr);
    assert(lv_obj_get_height(a.footer) <= a.footer_font.line_height);
    std::ofstream out(name, std::ios::binary);
    out << "P6\n320 170\n255\n";
    for (size_t i = 0; i < pixels.size(); i += 2) {
        unsigned value = pixels[i] | (pixels[i + 1] << 8);
        char rgb[] = {static_cast<char>(((value >> 11) & 31) * 255 / 31),
                      static_cast<char>(((value >> 5) & 63) * 255 / 63),
                      static_cast<char>((value & 31) * 255 / 31)};
        out.write(rgb, 3);
    }
}
}
int main() {
    lv_init();
    lv_tick_set_cb(ticks);
    auto *display = lv_display_create(320, 170);
    std::vector<uint8_t> buffer(320 * 170 * 2);
    lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(display, buffer.data(), nullptr, buffer.size(), LV_DISPLAY_RENDER_MODE_FULL);
    lv_display_set_flush_cb(display, flush);
    App a;
    create_ui(a);
    assert(a.cjk != nullptr);
    lv_font_glyph_dsc_t glyph{};
    assert(lv_font_get_glyph_dsc(&a.footer_font, &glyph, 0x21B5, 0) && !glyph.is_placeholder);
    a.status = "就绪 | ↵ 开始录音";
    snapshot(a, "home.ppm");
    assert(std::string(lv_label_get_text(a.footer)) == "↵ Talk | S Settings | C Clear | Esc Exit");
    a.user = "你好，介绍一下你自己。";
    a.answer = "你好！我是 MiMo，你的语音助手。可以陪你聊天、回答问题。";
    a.state = "working";
    a.status = "正在流式播报... | Esc 取消";
    snapshot(a, "chat.ppm");
    a.state = "idle";
    keypress(a, KEY_S, 1);
    assert(a.settings);
    a.ready = true;
    a.config[0] = "sample-key";
    a.config[1] = "https://api.xiaomimimo.com/v1";
    a.config[2] = "mimo-v2.6-flash";
    a.config[3] = "mimo-v2.5-asr";
    a.status = "配置保存在 ~/mimo_talk/config.json";
    snapshot(a, "settings.ppm");
    keypress(a, KEY_ENTER, 1);
    assert(a.edit);
    assert(lv_textarea_get_password_mode(a.input_box));
    keypress(a, KEY_LEFTCTRL, 1);
    keypress(a, KEY_U, 1);
    keypress(a, KEY_LEFTCTRL, 0);
    keypress(a, KEY_A, 1);
    keypress(a, KEY_LEFTSHIFT, 1);
    keypress(a, KEY_MINUS, 1);
    keypress(a, KEY_B, 1);
    keypress(a, KEY_LEFTSHIFT, 0);
    keypress(a, KEY_1, 1);
    assert(std::string(lv_textarea_get_text(a.input_box)) == "a_B1");
    snapshot(a, "edit.ppm");
    keypress(a, KEY_ESC, 1);
    assert(!a.edit && a.config[0] == "sample-key");
    keypress(a, KEY_X, 1);
    keypress(a, KEY_ENTER, 1);
    assert(a.edit && std::string(lv_textarea_get_text(a.input_box)) == a.config[1]);
    assert(unhex(hex("中文\n\t")) == "中文\n\t");
    lv_obj_clean(lv_screen_active());
    lv_tiny_ttf_destroy(a.cjk);
    lv_deinit();
}
