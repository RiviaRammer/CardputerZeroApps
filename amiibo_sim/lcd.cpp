#include "lvgl.h"
#include "st25r3916.h"
#include "src/drivers/display/fb/lv_linux_fbdev.h"

#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <linux/input.h>
#include <pty.h>
#include <string>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace {
namespace fs = std::filesystem;
constexpr char lib[] = "/usr/lib/amiibo_sim/";
constexpr const char *key_names[] = {"key_retail.bin", "retail.bin"};
constexpr char uid_pool_size[] = "256";
constexpr char spi_node[] = "/dev/spidev0.2";
constexpr char cap_setup_command[] = "sudo /usr/lib/amiibo_sim/cap-setup";
constexpr uint32_t cap_poll_ms = 2000;
volatile sig_atomic_t stopping = 0;

enum class Job { None, Setup, Probe, Pool, Emulate };

struct Child {
    pid_t pid = -1;
    int output = -1;
    int keyboard = -1;
    std::string pending;
};

struct BrowserEntry {
    fs::path path;
    bool is_folder;
};

struct App {
    fs::path root_folder;
    fs::path folder;
    fs::path active;
    fs::path working_dir;
    fs::path key_file;
    std::vector<BrowserEntry> entries;
    size_t selected = 0;
    bool folder_accessible = false;
    bool key_ready = false;
    bool cap_ready = false;
    bool cap_needs_setup_command = false;
    uint32_t last_cap_check = 0;
    bool cap_power_unconfirmed = false;
    bool started = false;
    bool has_pool = false;
    bool cancel_job = false;
    Job job = Job::None;
    Child child;
    lv_font_t *cjk = nullptr;
    lv_font_t filename_font{};
    lv_obj_t *rows[4]{};
    lv_obj_t *folder_label = nullptr;
    lv_obj_t *key = nullptr;
    lv_obj_t *cap = nullptr;
    lv_obj_t *status = nullptr;
    lv_obj_t *detail = nullptr;
    lv_obj_t *footer = nullptr;
};

void on_signal(int) { stopping = 1; }

uint32_t ticks()
{
    timespec time{};
    clock_gettime(CLOCK_MONOTONIC, &time);
    return static_cast<uint32_t>(time.tv_sec * 1000ULL + time.tv_nsec / 1000000);
}

std::string clip(std::string value, size_t limit)
{
    if (value.size() <= limit) return value;
    size_t keep = limit - 3;
    while (keep && (static_cast<unsigned char>(value[keep]) & 0xC0) == 0x80) --keep;
    return value.substr(0, keep) + "...";
}

void label(lv_obj_t *object, const std::string &text)
{
    if (text != lv_label_get_text(object)) lv_label_set_text(object, text.c_str());
}

lv_obj_t *make_label(lv_obj_t *parent, int x, int y, int width,
                     const lv_font_t *font, uint32_t color)
{
    lv_obj_t *object = lv_label_create(parent);
    lv_obj_set_pos(object, x, y);
    lv_obj_set_width(object, width);
    lv_obj_set_style_text_font(object, font, 0);
    lv_obj_set_style_text_color(object, lv_color_hex(color), 0);
    lv_label_set_long_mode(object, LV_LABEL_LONG_CLIP);
    return object;
}

void reset_selection_hint(App &app)
{
    lv_obj_set_style_text_color(app.status, lv_color_hex(0xF1F7FE), 0);
    const bool at_root = app.folder == app.root_folder;
    const char *hint = "Select an Amiibo backup";
    if (!app.folder_accessible) hint = "Cannot open this folder";
    else if (app.entries.empty()) {
        hint = at_root ? "Put .bin files in ~/amiibo_sim/" : "Put .bin files in this folder";
    } else if (app.entries[app.selected].is_folder) hint = "Enter to open folder";
    label(app.status, hint);
    label(app.detail, app.cap_needs_setup_command ? cap_setup_command :
                       at_root && app.entries.empty() && app.folder_accessible ?
                           "Optional: key_retail.bin or retail.bin" : "");
    if (app.key_ready) {
        label(app.key, "KEY  Found");
        lv_obj_set_style_text_color(app.key, lv_color_hex(0x6EF1B4), 0);
    }
}

void draw(App &app)
{
    // LCD and NFC share SPI0. Scrolling labels otherwise keep the display
    // transferring pixels throughout time-sensitive card emulation.
    const bool browsing = app.job != Job::Pool && app.job != Job::Emulate;
    const bool at_root = app.folder == app.root_folder;
    if (browsing) {
        const auto relative = app.folder.lexically_relative(app.root_folder);
        const std::string path = at_root ? "~/amiibo_sim/" : "~/amiibo_sim/" + relative.string() + "/";
        label(app.folder_label, path);
        lv_obj_remove_flag(app.folder_label, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_long_mode(app.folder_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    } else {
        lv_label_set_long_mode(app.folder_label, LV_LABEL_LONG_CLIP);
        lv_obj_add_flag(app.folder_label, LV_OBJ_FLAG_HIDDEN);
    }
    size_t first = app.selected >= 4 ? app.selected - 3 : 0;
    for (size_t i = 0; i < 4; ++i) {
        size_t index = first + i;
        bool selected = index == app.selected && index < app.entries.size();
        if (index < app.entries.size()) {
            const auto &entry = app.entries[index];
            label(app.rows[i], (selected ? " >  " : "     ") + entry.path.filename().string() +
                               (entry.is_folder ? "/" : ""));
        } else {
            label(app.rows[i], app.entries.empty() && app.folder_accessible && i == 0 ?
                               "   No Amiibo backups" : "");
        }
        lv_label_set_long_mode(app.rows[i], selected && browsing ? LV_LABEL_LONG_SCROLL_CIRCULAR : LV_LABEL_LONG_DOT);
        lv_obj_set_style_bg_opa(app.rows[i], selected ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
        lv_obj_set_style_text_color(app.rows[i], lv_color_hex(selected ? 0xECFFFA : 0xBBCBDD), 0);
    }
    std::string keys;
    if (app.job == Job::Emulate) {
        keys = app.has_pool ? "R New UID   O Original   ESC Stop" : "O Original   ESC Stop";
    } else if (app.job == Job::Pool) {
        keys = "ESC Cancel";
    } else {
        if (!app.entries.empty()) {
            keys = app.entries[app.selected].is_folder ? "F/X Move  Enter Open  " : "F/X Move  Enter Start  ";
        }
        keys += at_root ? "T Scan  H Cap  Esc Exit" : "T Scan  H Cap  Esc Back";
    }
    label(app.footer, keys);
}

void scan(App &app, fs::path previous = {})
{
    std::error_code error;
    if (previous.empty() && app.selected < app.entries.size()) previous = app.entries[app.selected].path;
    if (app.folder == app.root_folder) fs::create_directories(app.root_folder, error);
    app.entries.clear();
    app.key_file.clear();
    bool key_found = false;
    for (const char *name : key_names) {
        const fs::path path = app.root_folder / name;
        error.clear();
        const bool exists = fs::exists(path, error);
        key_found |= exists || static_cast<bool>(error);
        if (exists && fs::is_regular_file(path, error) && fs::file_size(path, error) == 160 &&
            !error && access(path.c_str(), R_OK) == 0) {
            app.key_file = path;
            break;
        }
    }
    app.key_ready = !app.key_file.empty();
    label(app.key, app.key_ready ? "KEY  Found" : key_found ? "KEY  Check file" : "KEY  Missing");
    lv_obj_set_style_text_color(app.key, lv_color_hex(app.key_ready ? 0x6EF1B4 : 0xFFC483), 0);

    fs::directory_iterator entries(app.folder, error);
    if (!error) {
        for (const auto end = fs::directory_iterator{}; entries != end; entries.increment(error)) {
            const auto &entry = *entries;
            std::error_code file_error;
            const auto type = entry.symlink_status(file_error);
            if (fs::is_directory(type)) {
                app.entries.push_back({entry.path(), true});
                continue;
            }
            std::string extension = entry.path().extension().string();
            std::transform(extension.begin(), extension.end(), extension.begin(),
                           [](unsigned char ch) { return ch >= 'A' && ch <= 'Z' ? ch + ('a' - 'A') : ch; });
            if (extension != ".bin" || entry.path().filename() == key_names[0] ||
                entry.path().filename() == key_names[1]) continue;
            if (entry.is_regular_file(file_error) && entry.file_size(file_error) == 540 && !file_error) {
                app.entries.push_back({entry.path(), false});
            }
        }
    }
    app.folder_accessible = !error;
    std::sort(app.entries.begin(), app.entries.end(), [](const BrowserEntry &left, const BrowserEntry &right) {
        if (left.is_folder != right.is_folder) return left.is_folder;
        return left.path < right.path;
    });
    const auto match = std::find_if(app.entries.begin(), app.entries.end(), [&](const BrowserEntry &entry) {
        return entry.path == previous;
    });
    app.selected = match == app.entries.end() ? 0 : static_cast<size_t>(match - app.entries.begin());
    reset_selection_hint(app);
    draw(app);
}

void create_ui(App &app)
{
    app.cjk = lv_tiny_ttf_create_file("A:/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 14);
    app.filename_font = lv_font_montserrat_14;
    app.filename_font.fallback = app.cjk;
    auto *screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_hex(0x101820), 0);
    app.folder_label = make_label(screen, 12, 7, 186, &app.filename_font, 0xF2F7FC);
    lv_label_set_long_mode(app.folder_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    app.key = make_label(screen, 208, 7, 108, &lv_font_montserrat_12, 0xFFC483);
    app.cap = make_label(screen, 13, 32, 300, &lv_font_montserrat_12, 0x72D8D3);
    label(app.cap, "CAP  Checking");
    for (int i = 0; i < 4; ++i) {
        app.rows[i] = make_label(screen, 9, 47 + i * 17, 302, &app.filename_font, 0xBBCBDD);
        lv_obj_set_style_radius(app.rows[i], 4, 0);
        lv_obj_set_style_bg_color(app.rows[i], lv_color_hex(0x22544F), 0);
    }
    auto *divider = lv_obj_create(screen);
    lv_obj_remove_style_all(divider);
    lv_obj_set_pos(divider, 10, 116);
    lv_obj_set_size(divider, 300, 1);
    lv_obj_set_style_bg_color(divider, lv_color_hex(0x405464), 0);
    lv_obj_set_style_bg_opa(divider, LV_OPA_COVER, 0);

    app.status = make_label(screen, 13, 120, 298, &lv_font_montserrat_12, 0xF1F7FE);
    app.detail = make_label(screen, 13, 135, 298, &lv_font_montserrat_12, 0x9CB2CB);
    app.footer = make_label(screen, 13, 153, 298, &lv_font_montserrat_12, 0xA2E9DF);
    scan(app);
}

int open_keyboard()
{
    int fallback = -1;
    std::error_code error;
    fs::directory_iterator entries("/dev/input", error);
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
            (bits[KEY_F / word] & (1UL << (KEY_F % word)))) {
            fallback = fd;
        } else {
            close(fd);
        }
    }
    return fallback;
}

bool spawn(App &app, Job job, const std::vector<std::string> &args)
{
    int pipe_fd[2], master = -1, slave = -1;
    if (pipe2(pipe_fd, O_CLOEXEC) < 0) return false;
    if (job == Job::Emulate && openpty(&master, &slave, nullptr, nullptr, nullptr) < 0) {
        close(pipe_fd[0]);
        close(pipe_fd[1]);
        return false;
    }
    if (master >= 0) {
        fcntl(master, F_SETFD, FD_CLOEXEC);
        fcntl(slave, F_SETFD, FD_CLOEXEC);
        termios settings{};
        if (tcgetattr(slave, &settings) == 0) {
            settings.c_lflag &= ~ECHO;
            tcsetattr(slave, TCSANOW, &settings);
        }
    }
    std::vector<char *> argv;
    for (const auto &arg : args) argv.push_back(const_cast<char *>(arg.c_str()));
    argv.push_back(nullptr);

    const pid_t parent = getpid();
    const pid_t pid = fork();
    if (pid == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGTERM) < 0 || getppid() != parent) _exit(126);
        int input = slave < 0 ? open("/dev/null", O_RDONLY) : slave;
        if (input < 0 || dup2(input, STDIN_FILENO) < 0 ||
            dup2(pipe_fd[1], STDOUT_FILENO) < 0 || dup2(pipe_fd[1], STDERR_FILENO) < 0) _exit(126);
        if (slave < 0) close(input);
        execv(argv[0], argv.data());
        _exit(127);
    }
    close(pipe_fd[1]);
    if (slave >= 0) close(slave);
    if (pid < 0) {
        close(pipe_fd[0]);
        if (master >= 0) close(master);
        return false;
    }
    fcntl(pipe_fd[0], F_SETFL, O_NONBLOCK);
    app.job = job;
    app.child = {pid, pipe_fd[0], master, {}};
    return true;
}

void cleanup_pool(App &app)
{
    if (app.working_dir.empty()) return;
    std::error_code error;
    fs::remove(app.working_dir / "uid-pool.bin", error);
    fs::remove(app.working_dir, error);
    app.working_dir.clear();
}

void setup_cap(App &app, bool show_progress = true)
{
    app.last_cap_check = ticks();
    app.cap_ready = false;
    app.cap_power_unconfirmed = false;
    if (show_progress) label(app.cap, "CAP  Checking");
    if (!spawn(app, Job::Setup, {std::string(lib) + "cap-setup"})) {
        label(app.cap, "CAP  Could not run setup");
    }
}

void start_emulator(App &app, bool pool)
{
    app.has_pool = pool;
    app.started = false;
    std::vector<std::string> args{std::string(lib) + "amiibo-zero", "emulate", app.active.string()};
    if (pool) {
        args.push_back("--uid-pool");
        args.push_back((app.working_dir / "uid-pool.bin").string());
    }
    label(app.status, "Starting NFC...");
    lv_obj_set_style_text_color(app.status, lv_color_hex(0xF1F7FE), 0);
    if (!spawn(app, Job::Emulate, args)) {
        label(app.status, "Could not start NFC");
        cleanup_pool(app);
    }
    draw(app);
}

void select_entry(App &app)
{
    if (app.entries.empty()) return;
    const auto &entry = app.entries[app.selected];
    if (entry.is_folder) {
        app.folder = entry.path;
        scan(app);
        return;
    }
    if (app.job != Job::None || !app.cap_ready) {
        label(app.status, "Waiting for Cap - H to check now");
        return;
    }
    reset_selection_hint(app);
    app.active = entry.path;
    app.cancel_job = false;
    label(app.detail, app.active.filename().string());
    if (app.key_ready) {
        char directory[] = "/tmp/amiibo-sim-XXXXXX";
        if (mkdtemp(directory)) {
            app.working_dir = directory;
            if (spawn(app, Job::Pool,
                      {"/usr/bin/python3", "-B", std::string(lib) + "make_uid_pool.py",
                       "--dump", app.active.string(), "--key", app.key_file.string(),
                       "--output", (app.working_dir / "uid-pool.bin").string(), "--count", uid_pool_size})) {
                label(app.status, "Preparing random UIDs...");
                draw(app);
                return;
            }
            cleanup_pool(app);
        }
        label(app.detail, "UID preparation unavailable");
    }
    start_emulator(app, false);
}

void child_line(App &app, const std::string &line)
{
    if (app.job != Job::Emulate) return;
    if (line.rfind("ST25R3916 detected", 0) == 0) {
        label(app.cap, "CAP  ST25R3916 connected");
    } else if (line.rfind("Emulating.", 0) == 0) {
        app.started = true;
        label(app.status, "Ready - hold Cap near the reader");
    } else if (line.rfind("UID ", 0) == 0) {
        label(app.detail, line);
    } else if (line == "Scan result: success") {
        label(app.status, "NFC replies sent - check Switch");
        lv_obj_set_style_text_color(app.status, lv_color_hex(0x6EF1B4), 0);
    } else if (line == "Scan result: failed") {
        label(app.status, "Read incomplete - try again");
        lv_obj_set_style_text_color(app.status, lv_color_hex(0xFFC483), 0);
    } else if (line.rfind("Ready:", 0) == 0) {
        label(app.status, line == "Ready: original dump restored." ? "Original UID restored" : "New UID ready");
        lv_obj_set_style_text_color(app.status, lv_color_hex(0xF1F7FE), 0);
    } else if (line.rfind("Unexpected ST25R3916", 0) == 0) {
        label(app.cap, "CAP  Chip not responding");
        label(app.detail, "Check EXT 5V and Cap seating");
    } else if (line.rfind("/dev/", 0) == 0 || line.rfind("NFC IRQ", 0) == 0 ||
               line.rfind("ST25R3916 ", 0) == 0 || line.rfind("Invalid NTAG215", 0) == 0) {
        label(app.detail, clip(line, 46));
    }
}

void read_output(App &app)
{
    char buffer[512];
    ssize_t size;
    while ((size = read(app.child.output, buffer, sizeof(buffer))) > 0) {
        app.child.pending.append(buffer, size);
        size_t newline;
        while ((newline = app.child.pending.find('\n')) != std::string::npos) {
            child_line(app, app.child.pending.substr(0, newline));
            app.child.pending.erase(0, newline + 1);
        }
        if (app.child.pending.size() > 4096) app.child.pending.clear();
    }
}

void finish_child(App &app, int status)
{
    read_output(app);
    close(app.child.output);
    if (app.child.keyboard >= 0) close(app.child.keyboard);
    Job job = app.job;
    app.child = Child{};
    app.job = Job::None;
    int code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
    if (job == Job::Setup) {
        app.cap_needs_setup_command = code == 13;
        switch (code) {
        case 0:
        case 15:
            app.cap_power_unconfirmed = code == 15;
            if (!spawn(app, Job::Probe, {std::string(lib) + "amiibo-zero", "probe"})) {
                label(app.cap, "CAP  Could not run chip check");
            }
            break;
        case 11: label(app.cap, "CAP  Set EXT D+/D- to GPIO in Settings"); break;
        case 12: label(app.cap, "CAP  SPI overlay missing. Update Zero OS"); break;
        case 13:
            label(app.cap, "CAP  NFC SPI missing - run in terminal:");
            break;
        case 14: label(app.cap, "CAP  EXT 5V failed - check Settings"); break;
        case 16:
            app.cap_needs_setup_command = true;
            label(app.cap, "CAP  SPI access denied - run in terminal:");
            break;
        default: label(app.cap, "CAP  Cannot read EXT port settings"); break;
        }
        if (app.job == Job::None) reset_selection_hint(app);
    } else if (job == Job::Probe) {
        app.cap_ready = code == CAP_PROBE_OK;
        app.cap_needs_setup_command = code == CAP_PROBE_PERMISSION || code == CAP_PROBE_NO_DEVICE ||
                                      (code == CAP_PROBE_NO_CHIP && app.cap_power_unconfirmed);
        switch (code) {
        case CAP_PROBE_OK:
            label(app.cap, "CAP  ST25R3916 connected");
            break;
        case CAP_PROBE_NO_DEVICE: label(app.cap, "CAP  SPI missing - press H to retry"); break;
        case CAP_PROBE_PERMISSION: label(app.cap, "CAP  SPI access denied - run in terminal:"); break;
        case CAP_PROBE_BUSY: label(app.cap, "CAP  SPI busy - close other NFC apps"); break;
        case CAP_PROBE_NO_CHIP:
            label(app.cap, app.cap_power_unconfirmed ? "CAP  Enable POWER_EN in terminal" :
                                                       "CAP  No chip - check EXT 5V and Cap");
            break;
        default: label(app.cap, "CAP  SPI check failed - press H to retry"); break;
        }
        reset_selection_hint(app);
    } else if (job == Job::Pool) {
        if (app.cancel_job || stopping) {
            cleanup_pool(app);
            reset_selection_hint(app);
        } else {
            if (code != 0) {
                cleanup_pool(app);
                label(app.key, "KEY  Sign failed");
                lv_obj_set_style_text_color(app.key, lv_color_hex(0xFFC483), 0);
                label(app.detail, "UID signing failed. Check key and dump");
            }
            start_emulator(app, code == 0);
        }
    } else if (job == Job::Emulate) {
        cleanup_pool(app);
        if (app.cancel_job || code == 0) {
            reset_selection_hint(app);
        } else {
            lv_obj_set_style_text_color(app.status, lv_color_hex(0xF1F7FE), 0);
            label(app.status, "NFC stopped - H to check Cap");
            if (std::strncmp(lv_label_get_text(app.detail), "UID ", 4) == 0) {
                label(app.detail, "");
            }
        }
    }
    draw(app);
}

void keypress(App &app, unsigned key)
{
    if (key == KEY_ESC) {
        if (app.job == Job::Pool || app.job == Job::Emulate) {
            app.cancel_job = true;
            kill(app.child.pid, SIGTERM);
        } else if (app.folder != app.root_folder) {
            const fs::path previous = app.folder;
            app.folder = previous.parent_path();
            scan(app, previous);
        } else {
            stopping = 1;
        }
        return;
    }
    if (app.job == Job::Emulate && app.started) {
        if (key == KEY_R && !app.has_pool) {
            label(app.detail, app.key_ready ? "UID unavailable. Check key and dump" : "Import key_retail.bin or retail.bin");
        } else if (key == KEY_R || key == KEY_O) {
            const char *command = key == KEY_R ? "r\n" : "o\n";
            if (write(app.child.keyboard, command, 2) == 2) {
                label(app.status, "Remove reader to apply UID");
            }
        }
        return;
    }
    if (key == KEY_T && (app.job == Job::None || app.job == Job::Setup || app.job == Job::Probe)) {
        scan(app);
        if (app.job == Job::None && !app.cap_ready) setup_cap(app);
        return;
    }
    if (app.job == Job::Pool || app.job == Job::Emulate) return;
    if (key == KEY_H && app.job == Job::None) setup_cap(app);
    if (key == KEY_ENTER || key == KEY_KPENTER) {
        select_entry(app);
        return;
    }
    const size_t previous = app.selected;
    if ((key == KEY_F || key == KEY_UP) && app.selected > 0) --app.selected;
    if ((key == KEY_X || key == KEY_DOWN) && app.selected + 1 < app.entries.size()) ++app.selected;
    if (app.selected != previous) reset_selection_hint(app);
    draw(app);
}
} // namespace

int main()
{
    const char *home = std::getenv("HOME");
    if (!home || !*home) return 1;
    App app;
    app.root_folder = fs::path(home) / "amiibo_sim";
    app.folder = app.root_folder;
    struct sigaction action{};
    action.sa_handler = on_signal;
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, nullptr);
    sigaction(SIGINT, &action, nullptr);

    lv_init();
    lv_tick_set_cb(ticks);
    auto *display = lv_linux_fbdev_create();
    const char *framebuffer = std::getenv("LV_LINUX_FBDEV_DEVICE");
    if (!framebuffer) framebuffer = std::getenv("APPLAUNCH_LINUX_FBDEV_DEVICE");
    if (!display || lv_linux_fbdev_set_file(display, framebuffer ? framebuffer : "/dev/fb0") != LV_RESULT_OK) {
        return 1;
    }
    create_ui(app);
    int keyboard = open_keyboard();
    if (keyboard < 0) {
        label(app.status, "Keyboard unavailable. Check input permission");
    }
    setup_cap(app);
    while (!stopping) {
        if (app.child.pid >= 0) {
            read_output(app);
            int status;
            if (waitpid(app.child.pid, &status, WNOHANG) == app.child.pid) finish_child(app, status);
        }
        input_event input{};
        while (keyboard >= 0 && read(keyboard, &input, sizeof(input)) == sizeof(input)) {
            if (input.type == EV_KEY && input.value == 1) keypress(app, input.code);
        }
        if (app.job == Job::None && ticks() - app.last_cap_check >= cap_poll_ms) {
            app.last_cap_check = ticks();
            if (!app.cap_ready || access(spi_node, R_OK | W_OK) < 0) setup_cap(app, false);
        }
        lv_timer_handler();
        usleep(10000);
    }
    if (app.child.pid >= 0) {
        kill(app.child.pid, SIGTERM);
        int status;
        while (waitpid(app.child.pid, &status, 0) < 0 && errno == EINTR) {}
    }
    cleanup_pool(app);
    if (keyboard >= 0) close(keyboard);
    lv_obj_clean(lv_screen_active());
    if (app.cjk) lv_tiny_ttf_destroy(app.cjk);
    lv_deinit();
    return 0;
}
