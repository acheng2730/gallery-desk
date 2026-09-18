#!/usr/bin/python3
"""Install launchers in the current user's home and start Gallery Desk."""

from pathlib import Path
import subprocess


def install():
    root = Path(__file__).resolve().parent
    user = Path.home()
    executable = user / ".local/bin/gallery-desk"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text(f'#!/bin/sh\nexec /usr/bin/python3 "{root / "app.py"}" "$@"\n')
    executable.chmod(0o755)
    desktop = user / ".local/share/applications/gallery-desk.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=Gallery Desk
Comment=Arrange your photos and Spotify turntable on the desktop
Exec={executable} --settings
Icon={root / "gallery-desk.svg"}
Terminal=false
Categories=Utility;
StartupNotify=true
StartupWMClass=GalleryDesk
Actions=Arrange;

[Desktop Action Arrange]
Name=Arrange widgets
Exec={executable} --arrange
''')
    autostart = user / ".config/autostart/gallery-desk.desktop"
    autostart.parent.mkdir(parents=True, exist_ok=True)
    autostart.write_text(f'''[Desktop Entry]
Type=Application
Name=Gallery Desk
Comment=Desktop photographs and Spotify turntable
Exec={executable} --background
Icon={root / "gallery-desk.svg"}
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
''')
    cache = user / ".cache/gallery-desk"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "session.log").open("a") as log:
        process = subprocess.Popen([str(executable), "--background"], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                   close_fds=True)
    print(f"Installed Gallery Desk; launch process {process.pid}.")


if __name__ == "__main__":
    install()
