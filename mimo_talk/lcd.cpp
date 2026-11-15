#include "lvgl.h"
#include "src/drivers/display/fb/lv_linux_fbdev.h"
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <linux/input.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace {
volatile sig_atomic_t stopping = 0;
void on_signal(int) { stopping = 1; }
uint32_t ticks() {
    timespec t{};
    clock_gettime(CLOCK_MONOTONIC, &t);
    return static_cast<uint32_t>(t.tv_sec * 1000ULL + t.tv_nsec / 1000000);
}
constexpr const char *fields[] = {"api_key", "base_url", "chat_model", "asr_model", "tts_model",
    "voice", "capture_device", "playback_device", "max_record_seconds", "max_completion_tokens",
    "history_turns", "system_prompt"};
constexpr int field_count = sizeof(fields) / sizeof(fields[0]);
constexpr const char *field_names[] = {"API Key", "API URL", "Chat model", "ASR model", "TTS model",
    "Voice", "Microphone", "Speaker", "Record limit (s)", "Reply tokens", "History turns", "System prompt"};

// Montserrat and Droid Sans Fallback lack U+21B5. Each byte is one row of the return arrow.
constexpr uint8_t return_bitmap[] = {0b00000001, 0b00000001, 0b00100001, 0b01000001,
                                     0b11111111, 0b01000000, 0b00100000};
constexpr lv_font_fmt_txt_glyph_dsc_t return_glyphs[] = {{}, {0, 10 * 16, 8, 7, 1, 1}};
constexpr lv_font_fmt_txt_cmap_t return_cmap = {0x21B5, 1, 1, nullptr, nullptr, 0, LV_FONT_FMT_TXT_CMAP_FORMAT0_TINY};
constexpr lv_font_fmt_txt_dsc_t return_font_data = {
    return_bitmap, return_glyphs, &return_cmap, nullptr, 0, 1, 1, 0, LV_FONT_FMT_TXT_PLAIN, 0
};

struct App {
    pid_t child = -1;
    int input = -1, output = -1;
    std::string pending, user, answer, status = "正在启动...", state = "idle";
    std::string config[field_count], editing;
    bool settings = false, edit = false, shift = false, caps = false, ctrl = false;
    bool fn = false, dirty = true, error = false, ready = false;
    int selected = 0;
    lv_font_t *cjk = nullptr;
    lv_font_t font{}, footer_font{}, return_font{};
    lv_obj_t *title{}, *badge{}, *panel{}, *body{}, *status_label{}, *footer{}, *input_box{};
};

std::string hex(const std::string &value) {
    constexpr char digits[] = "0123456789abcdef";
    std::string out;
    for (unsigned char c : value) { out += digits[c >> 4]; out += digits[c & 15]; }
    return out;
}
std::string unhex(const std::string &value) {
    auto digit = [](char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; };
    if (value.size() % 2) return {};
    std::string out;
    for (size_t i = 0; i < value.size(); i += 2) {
        int a = digit(value[i]), b = digit(value[i + 1]);
        if (a < 0 || b < 0) return {};
        out += static_cast<char>((a << 4) | b);
    }
    return out;
}
void command(App &a, const std::string &line) {
    if (a.input < 0) return;
    const std::string data = line + "\n";
    if (data.size() > 4096) {
        a.status = "内容过长，请直接编辑 config.json";
        a.error = true; a.dirty = true;
        return;
    }
    // Each command is shorter than PIPE_BUF; no partial writes to this nonblocking pipe.
    if (write(a.input, data.data(), data.size()) != static_cast<ssize_t>(data.size())) {
        a.status = "后台忙或已退出，请重新打开 MiMo";
        a.error = true;
    }
    a.dirty = true;
}
lv_obj_t *label(lv_obj_t *parent, int x, int y, int w, const lv_font_t *font, uint32_t color) {
    auto *obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_width(obj, w);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    return obj;
}
void create_ui(App &a) {
    const char *font_file = std::getenv("MIMO_FONT_FILE");
    a.cjk = lv_tiny_ttf_create_file(font_file ? font_file : "A:/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 14);
    a.return_font.get_glyph_dsc = lv_font_get_glyph_dsc_fmt_txt;
    a.return_font.get_glyph_bitmap = lv_font_get_bitmap_fmt_txt;
    a.return_font.dsc = &return_font_data;
    a.return_font.fallback = a.cjk;
    a.font = lv_font_montserrat_14;
    a.font.fallback = &a.return_font;
    a.footer_font = lv_font_montserrat_12;
    a.footer_font.fallback = &a.return_font;
    auto *screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x101820), 0);
    lv_obj_remove_flag(screen, LV_OBJ_FLAG_SCROLLABLE);
    a.title = label(screen, 9, 4, 220, &a.font, 0xF4FAFF);
    a.badge = label(screen, 238, 5, 78, &lv_font_montserrat_12, 0x6EF1B4);
    a.panel = lv_obj_create(screen);
    lv_obj_set_pos(a.panel, 6, 27);
    lv_obj_set_size(a.panel, 308, 96);
    lv_obj_set_style_bg_color(a.panel, lv_color_hex(0x182832), 0);
    lv_obj_set_style_border_width(a.panel, 0, 0);
    lv_obj_set_style_pad_all(a.panel, 5, 0);
    lv_obj_set_scroll_dir(a.panel, LV_DIR_VER);
    a.body = label(a.panel, 0, 0, 290, &a.font, 0xE6F0FA);
    a.status_label = label(screen, 8, 127, 304, &a.font, 0x8DE0D3);
    lv_label_set_long_mode(a.status_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_height(a.status_label, 18);
    a.footer = label(screen, 8, 151, 306, &a.footer_font, 0xA7B9CC);
    a.input_box = lv_textarea_create(screen);
    lv_obj_set_pos(a.input_box, 7, 65);
    lv_obj_set_size(a.input_box, 306, 46);
    lv_textarea_set_one_line(a.input_box, true);
    lv_textarea_set_max_length(a.input_box, 1800);
    lv_obj_set_style_text_font(a.input_box, &a.font, 0);
    lv_obj_set_style_bg_color(a.input_box, lv_color_hex(0x24483F), 0);
    lv_obj_set_style_text_color(a.input_box, lv_color_hex(0xFFFFFF), 0);
    lv_obj_add_flag(a.input_box, LV_OBJ_FLAG_HIDDEN);
}
void draw(App &a, bool follow = false) {
    lv_label_set_text(a.title, a.settings ? "MiMo Talk / 设置" : "MiMo Talk");
    lv_label_set_text(a.badge, a.state == "recording" ? "REC" : a.state == "working" ? "LIVE" : "READY");
    std::string body;
    if (a.settings) {
        if (a.edit) {
            body = std::string(field_names[a.selected]) + "\n输入新值 | ↵ 保存";
        } else {
            int first = a.selected >= 4 ? a.selected - 3 : 0;
            for (int i = first; i < std::min(first + 4, field_count); ++i) {
                std::string value = i == 0 ? (a.config[0].empty() ? "未设置" : "********") : a.config[i];
                if (value.size() > 22) {
                    size_t end = 19;
                    while (end && (static_cast<unsigned char>(value[end]) & 0xC0) == 0x80) --end;
                    value = value.substr(0, end) + "...";
                }
                body += (i == a.selected ? "> " : "  ") + std::string(field_names[i]) + ": " + value + "\n";
            }
        }
    } else if (a.user.empty() && a.answer.empty()) {
        body = "按 ↵ 开始录音，再按 ↵ 发送。\n配置文件 ~/mimo_talk/config.json\n先在系统 Settings 中连接 Wi-Fi。";
    } else {
        body = "你：" + a.user + "\n\nMiMo：" + a.answer;
    }
    lv_label_set_text(a.body, body.c_str());
    if (a.edit) lv_obj_remove_flag(a.input_box, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(a.input_box, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(a.status_label, a.status.c_str());
    lv_obj_set_style_text_color(a.status_label, lv_color_hex(a.error ? 0xFFB38A : 0x8DE0D3), 0);
    const char *footer = "↵ Talk | S Settings | C Clear | Esc Exit";
    if (a.settings) footer = a.edit ? "↵ Save | Esc Cancel | Ctrl+U Clear" : "F/X Move | ↵ Edit | Esc Back";
    else if (a.state == "recording") footer = "↵ Send | Esc Cancel";
    else if (a.state != "idle") footer = "Esc Cancel | F/X Scroll";
    lv_label_set_text(a.footer, footer);
    if (follow) {
        lv_obj_update_layout(a.panel);
        lv_obj_scroll_to_y(a.panel, lv_obj_get_scroll_bottom(a.panel) + lv_obj_get_scroll_y(a.panel), LV_ANIM_OFF);
    }
    a.dirty = false;
}

int open_keyboard() {
    int fallback = -1;
    std::error_code error;
    std::filesystem::directory_iterator entries("/dev/input", error);
    if (error) return -1;
    for (const auto &entry : entries) {
        if (entry.path().filename().string().rfind("event", 0) != 0) continue;
        int fd = open(entry.path().c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        char name[96]{};
        ioctl(fd, EVIOCGNAME(sizeof(name)), name);
        if (std::strstr(name, "tca8418")) {
            if (fallback >= 0) close(fallback);
            return fd;
        }
        unsigned long bits[KEY_MAX / (sizeof(unsigned long) * 8) + 1]{};
        constexpr size_t word = sizeof(unsigned long) * 8;
        if (fallback < 0 && ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(bits)), bits) >= 0 &&
            (bits[KEY_ENTER / word] & (1UL << (KEY_ENTER % word))) &&
            (bits[KEY_A / word] & (1UL << (KEY_A % word)))) fallback = fd;
        else close(fd);
    }
    return fallback;
}
char character(unsigned key, bool shift, bool caps) {
    const std::pair<unsigned, char> letters[] = {{KEY_Q,'q'}, {KEY_W,'w'}, {KEY_E,'e'}, {KEY_R,'r'},
        {KEY_T,'t'}, {KEY_Y,'y'}, {KEY_U,'u'}, {KEY_I,'i'}, {KEY_O,'o'}, {KEY_P,'p'}, {KEY_A,'a'},
        {KEY_S,'s'}, {KEY_D,'d'}, {KEY_F,'f'}, {KEY_G,'g'}, {KEY_H,'h'}, {KEY_J,'j'}, {KEY_K,'k'},
        {KEY_L,'l'}, {KEY_Z,'z'}, {KEY_X,'x'}, {KEY_C,'c'}, {KEY_V,'v'}, {KEY_B,'b'}, {KEY_N,'n'}, {KEY_M,'m'}};
    for (auto entry : letters) if (key == entry.first) return shift != caps ? entry.second - 32 : entry.second;
    if (key >= KEY_1 && key <= KEY_0) return (shift ? "!@#$%^&*()" : "1234567890")[key - KEY_1];
    const unsigned keys[] = {KEY_MINUS,KEY_EQUAL,KEY_LEFTBRACE,KEY_RIGHTBRACE,KEY_BACKSLASH,
                            KEY_SEMICOLON,KEY_APOSTROPHE,KEY_GRAVE,KEY_COMMA,KEY_DOT,KEY_SLASH,KEY_SPACE};
    const char *plain = "-=[]\\;'`,./ ";
    const char *upper = "_+{}|:\"~<>? ";
    for (size_t i = 0; i < sizeof(keys)/sizeof(keys[0]); ++i) if (key == keys[i]) return (shift ? upper : plain)[i];
    return 0;
}
void keypress(App &a, unsigned key, int value) {
    if (key == KEY_LEFTSHIFT || key == KEY_RIGHTSHIFT) { a.shift = value != 0; return; }
    if (key == KEY_LEFTCTRL || key == KEY_RIGHTCTRL) { a.ctrl = value != 0; return; }
    if (key == KEY_FN || key == KEY_RIGHTALT) { a.fn = value != 0; return; }
    if (!value) return;
    if (key == KEY_CAPSLOCK && value == 1) { a.caps = !a.caps; return; }
    if (a.edit) {
        if (key == KEY_ESC) { a.edit = false; a.dirty = true; return; }
        if (key == KEY_ENTER || key == KEY_KPENTER) {
            a.editing = lv_textarea_get_text(a.input_box);
            command(a, "set\t" + std::string(fields[a.selected]) + "\t" + hex(a.editing));
            a.edit = false;
            a.editing.clear();
            lv_textarea_set_text(a.input_box, "");
            return;
        }
        if (key == KEY_BACKSPACE) lv_textarea_delete_char(a.input_box);
        else if (a.ctrl && key == KEY_U) lv_textarea_set_text(a.input_box, "");
        else if (key == KEY_LEFT || (a.fn && key == KEY_Z)) lv_textarea_cursor_left(a.input_box);
        else if (key == KEY_RIGHT || (a.fn && key == KEY_C)) lv_textarea_cursor_right(a.input_box);
        else if (key == KEY_HOME) lv_textarea_set_cursor_pos(a.input_box, 0);
        else if (key == KEY_END) lv_textarea_set_cursor_pos(a.input_box, LV_TEXTAREA_CURSOR_LAST);
        else if (!a.ctrl && !a.fn) {
            char c = character(key, a.shift, a.caps);
            if (c && std::strlen(lv_textarea_get_text(a.input_box)) < 1800) lv_textarea_add_char(a.input_box, c);
        }
        return;
    }
    if (key == KEY_ESC && value == 1) {
        if (a.settings) { a.settings = false; lv_obj_scroll_to_y(a.panel, 0, LV_ANIM_OFF); }
        else if (a.state != "idle") command(a, "cancel");
        else stopping = 1;
    } else if (a.settings) {
        if (key == KEY_F || key == KEY_UP) a.selected = std::max(0, a.selected - 1);
        if (key == KEY_X || key == KEY_DOWN) a.selected = std::min(field_count - 1, a.selected + 1);
        if ((key == KEY_ENTER || key == KEY_KPENTER) && value == 1 && a.ready) {
            a.edit = true;
            lv_textarea_set_password_mode(a.input_box, a.selected == 0);
            lv_textarea_set_password_show_time(a.input_box, 0);
            lv_textarea_set_text(a.input_box, a.config[a.selected].c_str());
            lv_textarea_set_cursor_pos(a.input_box, LV_TEXTAREA_CURSOR_LAST);
        }
    } else if (a.state == "idle" && key == KEY_S && value == 1) {
        a.settings = true;
        a.ready = false;
        command(a, "get");
        lv_obj_scroll_to_y(a.panel, 0, LV_ANIM_OFF);
    } else if (a.state == "idle" && key == KEY_C && value == 1) command(a, "clear");
    else if ((key == KEY_ENTER || key == KEY_KPENTER) && value == 1) {
        if (a.state == "idle") command(a, "record");
        else if (a.state == "recording") command(a, "stop");
    } else if (key == KEY_F || key == KEY_UP) lv_obj_scroll_by(a.panel, 0, 40, LV_ANIM_OFF);
    else if (key == KEY_X || key == KEY_DOWN) lv_obj_scroll_by(a.panel, 0, -40, LV_ANIM_OFF);
    a.dirty = true;
}
bool spawn(App &a, const char *backend) {
    int in[2], out[2];
    if (pipe2(in, O_CLOEXEC) < 0) return false;
    if (pipe2(out, O_CLOEXEC) < 0) { close(in[0]); close(in[1]); return false; }
    pid_t parent = getpid();
    a.child = fork();
    if (a.child == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGTERM) < 0 || getppid() != parent) _exit(126);
        if (dup2(in[0], STDIN_FILENO) < 0 || dup2(out[1], STDOUT_FILENO) < 0) _exit(126);
        execl("/usr/bin/python3", "python3", "-B", "-u", backend, nullptr);
        _exit(127);
    }
    close(in[0]); close(out[1]);
    if (a.child < 0) { close(in[1]); close(out[0]); return false; }
    a.input = in[1]; a.output = out[0];
    fcntl(a.input, F_SETFL, O_NONBLOCK);
    fcntl(a.output, F_SETFL, O_NONBLOCK);
    return true;
}
void read_output(App &a) {
    char buffer[4096];
    ssize_t count;
    bool follow = false;
    while ((count = read(a.output, buffer, sizeof(buffer))) > 0) {
        a.pending.append(buffer, count);
        size_t end;
        while ((end = a.pending.find('\n')) != std::string::npos) {
            std::string line = a.pending.substr(0, end);
            a.pending.erase(0, end + 1);
            size_t tab = line.find('\t');
            if (tab == std::string::npos) continue;
            std::string kind = line.substr(0, tab), value = line.substr(tab + 1);
            if (kind == "config") {
                tab = value.find('\t');
                if (tab == std::string::npos) continue;
                const std::string key = unhex(value.substr(0, tab));
                for (int i = 0; i < field_count; ++i) if (key == fields[i]) a.config[i] = unhex(value.substr(tab + 1));
                a.ready = true;
            } else {
                value = unhex(value);
                if (kind == "status" || kind == "error") { a.status = value; a.error = kind == "error"; }
                else if (kind == "state") a.state = value;
                else if (kind == "user") { a.user = value; follow = !a.settings; }
                else if (kind == "answer") { a.answer = value; follow = !a.settings; }
            }
            a.dirty = true;
        }
        if (a.pending.size() > 131072) a.pending.clear();
    }
    if (a.dirty) draw(a, follow);
}
} // namespace

int main(int argc, char **argv) {
    struct sigaction action{};
    action.sa_handler = on_signal;
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, nullptr);
    sigaction(SIGINT, &action, nullptr);
    signal(SIGPIPE, SIG_IGN);
    lv_init();
    lv_tick_set_cb(ticks);
    auto *display = lv_linux_fbdev_create();
    const char *framebuffer = std::getenv("LV_LINUX_FBDEV_DEVICE");
    if (!framebuffer) framebuffer = std::getenv("APPLAUNCH_LINUX_FBDEV_DEVICE");
    if (!display || lv_linux_fbdev_set_file(display, framebuffer ? framebuffer : "/dev/fb0") != LV_RESULT_OK) return 1;
    App app;
    create_ui(app);
    int keyboard = open_keyboard();
    if (!spawn(app, argc > 1 ? argv[1] : "/usr/lib/mimo-talk/mimo.py")) {
        app.status = "无法启动后台"; app.error = true;
    }
    draw(app);
    while (!stopping) {
        if (app.child > 0) {
            read_output(app);
            int status;
            if (waitpid(app.child, &status, WNOHANG) == app.child) {
                close(app.input); close(app.output);
                app.input = app.output = -1; app.child = -1;
                app.state = "idle"; app.ready = false; app.error = true;
                app.status = "后台已退出，请重新打开 MiMo Talk"; app.dirty = true;
            }
        }
        input_event event{};
        while (keyboard >= 0 && read(keyboard, &event, sizeof(event)) == sizeof(event)) {
            if (event.type == EV_KEY) keypress(app, event.code, event.value);
        }
        if (keyboard < 0) { app.status = "键盘不可用，请检查 input 权限"; app.error = true; app.dirty = true; }
        if (app.dirty) draw(app);
        lv_timer_handler();
        usleep(10000);
    }
    if (app.child > 0) {
        close(app.input);
        kill(app.child, SIGTERM);
        // Python's signal handler stops ALSA children; bound shutdown even on backend failure.
        bool reaped = false;
        for (int i = 0; i < 150; ++i) {
            if (waitpid(app.child, nullptr, WNOHANG) == app.child) { reaped = true; break; }
            usleep(10000);
        }
        if (!reaped) { kill(app.child, SIGKILL); waitpid(app.child, nullptr, 0); }
        close(app.output);
    }
    if (keyboard >= 0) close(keyboard);
    lv_obj_clean(lv_screen_active());
    if (app.cjk) lv_tiny_ttf_destroy(app.cjk);
    lv_deinit();
    return 0;
}
