"""Cairo drawing shared by desktop windows, previews, and rendering tests."""

import math
import random
import time
from functools import lru_cache

import cairo
import gi
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Pango, PangoCairo

from core import contain_rect, format_time
from tonearm import PARKED_ANGLE


CREAM = (0.97, 0.90, 0.80)
MUTED = (0.73, 0.67, 0.60)
ACCENT = (0.94, 0.64, 0.40)


def rounded(context, x, y, width, height, radius):
    radius = min(radius, width / 2, height / 2)
    context.new_sub_path()
    for cx, cy, start in ((x + width - radius, y + radius, -math.pi / 2),
                          (x + width - radius, y + height - radius, 0),
                          (x + radius, y + height - radius, math.pi / 2),
                          (x + radius, y + radius, math.pi)):
        context.arc(cx, cy, radius, start, start + math.pi / 2)
    context.close_path()


def text(context, value, x, y, width, size=12, color=CREAM, bold=False, lines=1,
         align=Pango.Alignment.LEFT):
    layout = PangoCairo.create_layout(context)
    font = Pango.FontDescription.from_string(f"Ubuntu {'Bold' if bold else 'Regular'} {size}")
    layout.set_font_description(font)
    layout.set_width(round(width * Pango.SCALE))
    layout.set_height(-lines)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_alignment(align)
    layout.set_text(str(value), -1)
    context.move_to(x, y)
    context.set_source_rgb(*color)
    PangoCairo.show_layout(context, layout)
    return layout.get_pixel_size()[1]


def image_in_rect(context, picture, rectangle, radius=0, alpha=1):
    x, y, width, height = rectangle
    context.save()
    rounded(context, x, y, width, height, radius)
    context.clip()
    context.translate(x, y)
    context.scale(width / picture.get_width(), height / picture.get_height())
    Gdk.cairo_set_source_pixbuf(context, picture, 0, 0)
    context.get_source().set_filter(cairo.FILTER_BILINEAR)
    context.paint_with_alpha(alpha)
    context.restore()


def photo(context, width, height, current, incoming=None, fraction=0, radius=24):
    # Sum premultiplied images in an isolated group: overlapping opaque pixels
    # stay opaque at mid-fade instead of briefly revealing the wallpaper.
    context.push_group()
    context.set_operator(cairo.OPERATOR_ADD)
    if current is not None:
        image_in_rect(context, current,
                      contain_rect(current.get_width(), current.get_height(), width, height),
                      radius, 1 - fraction if incoming is not None else 1)
    if incoming is not None:
        image_in_rect(context, incoming,
                      contain_rect(incoming.get_width(), incoming.get_height(), width, height),
                      radius, fraction)
    context.pop_group_to_source()
    context.set_operator(cairo.OPERATOR_OVER)
    context.paint()


def circle(context, x, y, radius, color):
    context.arc(x, y, radius, 0, 2 * math.pi)
    context.set_source_rgba(*color)
    context.fill()


def transport(context, method, x, y, enabled=True, playing=False, hovered=False):
    context.save()
    context.translate(x, y)
    if hovered:
        circle(context, 0, 0, 28, (*CREAM, .14))
        context.arc(0, 0, 27, 0, 2 * math.pi)
        context.set_source_rgba(*CREAM, .36)
        context.set_line_width(1)
        context.stroke()
    context.set_source_rgba(*CREAM, 1 if enabled else .3)
    if method == "PlayPause":
        if playing:
            context.rectangle(-6, -8, 4, 16)
            context.rectangle(2, -8, 4, 16)
        else:
            context.move_to(-5, -9)
            context.line_to(9, 0)
            context.line_to(-5, 9)
            context.close_path()
    else:
        if method == "Previous":
            context.scale(-1, 1)
        context.move_to(-7, -7)
        context.line_to(3, 0)
        context.line_to(-7, 7)
        context.close_path()
        context.rectangle(4, -7, 3, 14)
    context.fill()
    context.restore()


def tonearm(context, angle):
    # The arm rests farther out when paused.
    context.save()
    context.translate(263, 77)
    context.rotate(angle)
    circle(context, 0, 0, 15, (.11, .115, .12, 1))
    circle(context, 0, 0, 10, (.60, .57, .52, 1))
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    context.move_to(0, -6)
    context.line_to(0, 93)
    context.line_to(-31, 131)
    context.set_source_rgb(.69, .67, .61)
    context.set_line_width(6)
    context.stroke_preserve()
    context.set_source_rgb(.89, .86, .79)
    context.set_line_width(1.4)
    context.stroke()
    context.translate(-31, 131)
    context.rotate(.63)
    rounded(context, -7, -4, 14, 25, 3)
    context.set_source_rgb(.25, .24, .23)
    context.fill()
    context.restore()


@lru_cache(maxsize=1)
def glass_grain():
    generator = random.Random(41)
    pixels = bytearray()
    for _ in range(64 * 64):
        alpha = generator.randrange(2, 10)
        pixels.extend((alpha, alpha, alpha, alpha))
    surface = cairo.ImageSurface.create_for_data(pixels, cairo.FORMAT_ARGB32, 64, 64)
    pattern = cairo.SurfacePattern(surface)
    pattern.set_extend(cairo.EXTEND_REPEAT)
    return pattern


def frosted_panel(context, width, height, radius):
    context.save()
    rounded(context, 0, 0, width, height, radius)
    context.clip()
    backdrop = cairo.LinearGradient(0, 0, width, height)
    backdrop.add_color_stop_rgba(0, .49, .465, .425, .36)
    backdrop.add_color_stop_rgba(1, .36, .355, .34, .30)
    context.set_source(backdrop)
    context.paint()
    context.set_source(glass_grain())
    context.paint_with_alpha(.22)
    context.restore()
    rounded(context, .7, .7, width - 1.4, height - 1.4, radius - .7)
    context.set_source_rgba(1, .95, .86, .34)
    context.set_line_width(1.2)
    context.stroke()


def player(context, width, height, media, angle=0, seek_preview=None, arm_angle=None,
           hover_control=None):
    context.save()
    context.scale(width / 640, height / 350)
    frosted_panel(context, 640, 350, 25)
    text(context, "SIDE A  /  NOW PLAYING", 25, 20, 400, 9, MUTED, True)
    if hover_control == "menu":
        circle(context, 611, 27, 20, (*CREAM, .10))
    for offset in (-4, 0, 4):
        circle(context, 611 + offset, 27, 1.1,
               (*CREAM, 1) if hover_control == "menu" else (*MUTED, 1))

    # Recessed platter, brushed rings, and record. Artwork rotates with the vinyl.
    circle(context, 157, 189, 130, (.035, .033, .03, .5))
    circle(context, 153, 184, 127, (.50, .45, .40, 1))
    circle(context, 153, 184, 123, (.13, .13, .125, 1))
    circle(context, 153, 184, 119, (.025, .028, .032, 1))
    context.set_line_width(.65)
    for radius in range(61, 117, 3):
        context.arc(153, 184, radius, 0, 2 * math.pi)
        context.set_source_rgba(.8, .79, .74, .085 if radius % 2 else .13)
        context.stroke()
    sheen = cairo.LinearGradient(40, 55, 245, 280)
    sheen.add_color_stop_rgba(0, 1, 1, 1, 0)
    sheen.add_color_stop_rgba(.35, 1, 1, 1, .07)
    sheen.add_color_stop_rgba(.5, 1, 1, 1, 0)
    sheen.add_color_stop_rgba(.85, 1, 1, 1, .04)
    context.arc(153, 184, 119, 0, 2 * math.pi)
    context.set_source(sheen)
    context.fill()
    context.save()
    context.translate(153, 184)
    context.rotate(angle)
    if media.art:
        image_in_rect(context, media.art, (-58, -58, 116, 116), 58)
    else:
        circle(context, 0, 0, 58, (.57, .33, .23, 1))
        text(context, "GALLERY", -52, -22, 104, 10, CREAM, True, align=Pango.Alignment.CENTER)
        text(context, "33 ⅓", -45, 14, 90, 9, CREAM, align=Pango.Alignment.CENTER)
    context.restore()
    circle(context, 153, 184, 5, (.83, .79, .70, 1))
    circle(context, 153, 184, 2, (.13, .14, .15, 1))

    tonearm(context, PARKED_ANGLE if arm_angle is None and not media.clock.playing
            else 0 if arm_angle is None else arm_angle)
    circle(context, 34, 318, 3, (*ACCENT, 1) if media.clock.playing else (*MUTED, .55))
    text(context, "33 ⅓   STEREO", 46, 310, 220, 8, MUTED)

    x, content_width = 324, 285
    if media.connected:
        title_height = text(context, media.title, x, 74, content_width, 20, CREAM, True, lines=2)
        text(context, media.artist or "Spotify", x, 84 + title_height, content_width, 12, ACCENT)
        text(context, media.metadata.get("xesam:album", ""), x, 110 + title_height,
             content_width, 10, MUTED)
    else:
        text(context, "Your listening\ncorner", x, 74, content_width, 22, CREAM, True, lines=2)
        text(context, "Open Spotify to start a record.", x, 158, content_width, 11, MUTED)
    position = media.clock.at(time.monotonic())
    fraction = position / media.clock.length if media.clock.length else 0
    if seek_preview is not None:
        fraction = seek_preview
        position = round(fraction * media.clock.length)
    rounded(context, x, 227, content_width, 4, 2)
    context.set_source_rgba(*CREAM, .28 if hover_control == "seek" else .15)
    context.fill()
    if fraction > 0:
        rounded(context, x, 227, max(4, content_width * fraction), 4, 2)
        context.set_source_rgb(*ACCENT)
        context.fill()
    circle(context, x + content_width * fraction, 229,
           5 if hover_control == "seek" else 4, (*CREAM, 1))
    text(context, format_time(position), x, 239, 120, 9, MUTED)
    text(context, format_time(media.clock.length), x + 165, 239, 120, 9, MUTED,
         align=Pango.Alignment.RIGHT)
    circle(context, 466, 292, 25, (.78, .50, .32, .82) if hover_control == "play"
           else (.66, .43, .30, .65))
    for method, cx, permission in (("Previous", 403, "CanGoPrevious"),
                                    ("PlayPause", 466, "CanPause" if media.clock.playing else "CanPlay"),
                                    ("Next", 529, "CanGoNext")):
        transport(context, method, cx, 292, media.properties.get(permission, False),
                  media.clock.playing, hover_control == {
                      "Previous": "previous", "PlayPause": "play", "Next": "next"
                  }[method])
    text(context, "SPOTIFY", 551, 316, 64, 8,
         CREAM if hover_control == "spotify" else MUTED, True, align=Pango.Alignment.RIGHT)
    if media.error:
        text(context, media.error, 324, 52, 285, 8, ACCENT)
    context.restore()


def visualizer(context, width, height, levels, peaks, status="LIVE"):
    context.save()
    context.scale(width / 640, height / 160)
    frosted_panel(context, 640, 160, 20)
    text(context, "SIDE B  /  SPECTRUM", 25, 16, 270, 9, MUTED, True)
    text(context, status, 366, 17, 212, 8, ACCENT if status == "LIVE" else MUTED,
         align=Pango.Alignment.RIGHT)
    for offset in (-4, 0, 4):
        circle(context, 611 + offset, 23, 1.1, (*MUTED, 1))

    rounded(context, 18, 42, 604, 90, 9)
    context.set_source_rgba(.025, .03, .03, .35)
    context.fill()
    left, top, plot_width, plot_height = 27, 49, 586, 76
    step = plot_width / len(levels)
    bar_width = step - 4
    segments, gap = 12, 2
    segment_height = (plot_height - (segments - 1) * gap) / segments
    glow = cairo.LinearGradient(0, top + plot_height, 0, top)
    glow.add_color_stop_rgb(0, .69, .34, .16)
    glow.add_color_stop_rgb(.55, *ACCENT)
    glow.add_color_stop_rgb(1, 1, .88, .66)
    for index, (level, peak) in enumerate(zip(levels, peaks)):
        x = left + index * step + 2
        lit = round(max(0, min(1, level)) * segments)
        for segment in range(segments):
            y = top + plot_height - segment_height - segment * (segment_height + gap)
            context.rectangle(x, y, bar_width, segment_height)
            if segment < lit:
                context.set_source(glow)
            else:
                context.set_source_rgba(*ACCENT, .065)
            context.fill()
        if peak > .02:
            context.rectangle(x, top + (1 - max(0, min(1, peak))) * plot_height, bar_width, 1.5)
            context.set_source_rgba(*CREAM, .85)
            context.fill()
    for frequency, label in ((50, "50"), (100, "100"), (250, "250"),
                             (1000, "1k"), (4000, "4k"), (16000, "16k Hz")):
        fraction = math.log(frequency / 50) / math.log(16000 / 50)
        x = max(25, min(565, left + fraction * plot_width - 25))
        text(context, label, x, 137, 50, 8, MUTED,
             align=Pango.Alignment.LEFT if frequency == 50 else
                   Pango.Alignment.RIGHT if frequency == 16000 else Pango.Alignment.CENTER)
    context.restore()


def arrange_overlay(context, width, height, label, grid_spacing=None, grid_offset=(0, 0)):
    if grid_spacing:
        spacing = max(1, float(grid_spacing))
        context.save()
        rounded(context, 1.5, 1.5, width - 3, height - 3, 10)
        context.clip()
        context.set_source_rgba(.98, .78, .52, .16)
        context.set_line_width(.7)
        x = grid_offset[0] % spacing
        while x < width:
            context.move_to(x, 0)
            context.line_to(x, height)
            x += spacing
        y = grid_offset[1] % spacing
        while y < height:
            context.move_to(0, y)
            context.line_to(width, y)
            y += spacing
        context.stroke()
        context.restore()
    rounded(context, 1.5, 1.5, width - 3, height - 3, 10)
    context.set_source_rgba(.98, .78, .52, .95)
    context.set_line_width(2)
    context.set_dash([7, 5])
    context.stroke()
    context.set_dash([])
    rounded(context, 8, 8, min(width - 16, 240), 32, 8)
    context.set_source_rgba(.10, .09, .08, .9)
    context.fill()
    text(context, label + "  ·  drag to move", 17, 15, min(width - 35, 220), 10)
    rounded(context, width - 30, height - 30, 26, 26, 6)
    context.set_source_rgba(.10, .09, .08, .9)
    context.fill()
    context.set_line_width(2)
    context.set_source_rgb(*ACCENT)
    for offset in (8, 14, 20):
        context.move_to(width - offset, height - 7)
        context.line_to(width - 7, height - offset)
        context.stroke()
