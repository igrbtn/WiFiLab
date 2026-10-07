/* WiFiLab.app native launcher.
 *
 * Embeds CPython via libpython instead of exec'ing the venv interpreter: the process keeps the bundle identity
 * (the executable stays inside WiFiLab.app), so LSUIElement applies, the Location Services prompt and its
 * permission belong to "WiFiLab", and the menubar item belongs to the right app.
 *
 * PYHOME and SITE_PACKAGES are injected at compile time by build_app.sh.
 */

#include <stdio.h>
#include <stdlib.h>

extern int Py_BytesMain(int argc, char **argv);

int main(int argc, char **argv) {
    char buf[4096];
    const char *home = getenv("HOME");
    if (home) {
        snprintf(buf, sizeof buf, "%s/Library/Logs/WiFiLab.log", home);
        freopen(buf, "a", stdout);
        freopen(buf, "a", stderr);
        setvbuf(stdout, NULL, _IONBF, 0);
        setvbuf(stderr, NULL, _IONBF, 0);
    }

    setenv("PYTHONHOME", PYHOME, 1);
    setenv("PYTHONPATH", SITE_PACKAGES, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);

    char *args[] = {argv[0], "-c", "from wifilab.cli import menubar; menubar()", NULL};
    return Py_BytesMain(3, args);
}
