# Gallery Desk

Four independently changing photo frames, a Spotify turntable, and an audio spectrum, arranged around
your desktop wallpaper with your existing Conky system graphs. Starts automatically
when you sign in. Find **Gallery Desk** in Applications to open settings.

## Installation

This application requires **Python 3** (tested with Python 3.12).

Clone this repository and run the installation script:
```sh
git clone https://github.com/acheng2730/gallery-desk.git
cd gallery-desk
python3 install.py
```
The installer automatically sets up a launcher in your Applications menu and configures Gallery Desk to autostart via `~/.config/autostart/gallery-desk.desktop` when you sign in.


## Everyday controls

- **Arrange Widgets**: Open the **Gallery Desk** app from your application launcher and select **Arrange**. Drag a widget to move it; drag the lower-right corner of a photo, turntable, or visualizer to resize. The system graphs can also be moved in this mode. Click **Done** or press Escape while the arrangement toolbar has focus. Positions and sizes save automatically. The Arrange toolbar's **Snap** switch shows a faint grid and snaps nearby edges and centers. Equal gaps between neighboring widgets take priority; the pixel field controls the fallback grid spacing.
- **Settings**: Open the **Gallery Desk** app to change the photo folder, interval range, fade duration, corner radius, number of frames, and individual sizes. Up to 12 frames are supported.
- Click the turntable's previous, play/pause, and next buttons to control Spotify.
  Click or drag the progress bar to seek. Click **SPOTIFY** to open Spotify.
  The three dots open the widget menu. These controls work directly on the
  desktop outside Arrange mode; the record and background let clicks through.
  Controls brighten on hover, and the tonearm glides between its parked and
  playing positions when playback or tracks change.
- The audio visualizer displays 32 frequency bands from 50 Hz to 16 kHz with
  smooth decay and falling peak markers. It follows the desktop's default audio
  output, including Spotify, browsers, and other apps. **Settings → Show spectrum**
  hides or enables it; **Visualizer width** changes its size.

Moving the system graphs briefly restarts Conky, so its graph history rebuilds.
This avoids a crash in the installed Conky version when it reloads its layout.

## Features

### Photo Frames
The application selects random photographs from your configured photo directory.
Each frame independently chooses a fresh random delay
after every photo change, from the configured range (15–30 seconds by default).
Set both ends to the same value for a fixed interval. The original files are never changed.
EXIF orientation is respected; each image fits inside its frame without cropping
or background bars. The square arrangement outline marks the reserved footprint;
it disappears when you finish arranging. Both outgoing and incoming photographs
are reserved globally so frames do not display the same photo simultaneously.
With too few photographs, a frame waits rather than duplicates another frame.

### Spotify Turntable
Spotify uses the desktop application's existing login and local playback interface.
Open the Spotify desktop app to connect; browser-only Spotify sessions are not
supported. Album artwork is cached locally. The record turns while music plays.

### Audio Visualizer
The visualizer reads stereo samples from PipeWire's output monitor into memory;
it does not capture the microphone or save audio. Its passive connection lets
the audio device suspend when idle. Bars settle when playback stops, and capture
reconnects automatically if the audio service restarts. System output volume may
not affect monitor levels, depending on the audio device's monitor configuration.

### Frosted Backgrounds
The turntable and spectrum use frosted backgrounds: a cached 16-pixel blur of
the zoomed desktop wallpaper, a light gray tint, and subtle grain. The wallpaper
stays aligned as either widget moves or resizes; text and controls remain sharp.

## Conky Integration

This repository includes a custom Conky configuration located in the `conky-config/` directory.
- **Installation:** To use it, copy the contents of `conky-config/` to `~/.config/conky/`.
- **Interfacing:** Gallery Desk automatically detects and integrates with the active Conky graphs. During Arrange mode (launched from the Gallery Desk app), Gallery Desk exposes the position of the Conky graphs, allowing you to drag and snap them alongside your photo frames and turntable. When you finish arranging, Gallery Desk writes the new layout coordinates back to your Conky configuration files and restarts the Conky process to apply the changes smoothly without crashing. Backups of original Conky configurations are saved in `~/.config/gallery-desk/backups/`.

## Files and recovery

- Settings: `~/.config/gallery-desk/settings.json`
- Application: `~/.local/share/gallery-desk/`
- Launcher: `~/.local/bin/gallery-desk`
- Autostart: `~/.config/autostart/gallery-desk.desktop`
- Image cache and logs: `~/.cache/gallery-desk/`
- Original Conky configuration: `~/.config/gallery-desk/backups/conky-system.conf`

Quit from a widget's context menu, or run `gallery-desk --quit`. Launch it again
with `gallery-desk --background`. The application launcher opens settings, and
`gallery-desk --arrange` opens arrangement mode. The previous Picture desktop
widget extension remains installed and can be re-enabled in GNOME Extensions.
Disable Gallery Desk's autostart entry if you prefer to return to that widget.

## Platform Support

This is a Python 3.12 / GTK 3.24 desktop application designed specifically for Linux desktop environments (tested on GNOME 46 Wayland). **It is not supported on Windows or macOS.**

On Wayland it uses transparent Xwayland desktop windows, which support positioning and stay below normal application windows. The turntable uses one ordinary window for both drawing and input, so covering it also covers its controls. It temporarily uses a dock layer during Show Desktop and returns to the ordinary layer when apps return. During arrangement they temporarily become ordinary windows above the desktop, so desktop icons cannot intercept their drag handles.

It needs the system PyGObject, libwnck, Cairo, Pillow, NumPy, and PipeWire's `pw-cat` packages; no extra package installation is needed.

## CLI Usage

`gallery-desk --status` reports the live photo and player state.
`gallery-desk --snapshot` creates a layout preview over the configured wallpaper; the preview does not include other open applications or desktop icons.

API references: [GTK desktop window hints](https://docs.gtk.org/gtk3/method.Window.set_type_hint.html), [MPRIS player interface](https://specifications.freedesktop.org/mpris/latest/Player_Interface.html), [PipeWire monitor stream keys](https://docs.pipewire.org/group__pw__keys.html), [PipeWire passive nodes](https://docs.pipewire.org/page_man_pipewire-props_7.html).
