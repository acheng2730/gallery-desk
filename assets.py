"""Bounded background loading for local photographs and Spotify artwork."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from pathlib import Path
import urllib.parse
import urllib.request

from PIL import Image, ImageCms, ImageOps
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, GdkPixbuf


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
# The user's inspected collection contains one 149.8 MP camera image.
Image.MAX_IMAGE_PIXELS = 160_000_000


def scan_photos(directory):
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"Photo folder does not exist: {root}")
    return sorted(str(path) for path in root.rglob("*")
                  if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
                  and not any(part.startswith(".") for part in path.relative_to(root).parts))


def upright_thumbnail(source, destination, edge=1024):
    with Image.open(source) as original:
        profile = original.info.get("icc_profile")
        # Decode large JPEGs at reduced resolution before applying orientation.
        original.draft("RGB", (edge, edge))
        picture = ImageOps.exif_transpose(original)
        alpha = picture.getchannel("A") if "A" in picture.getbands() else None
        picture = picture.convert("RGB")
        if profile:
            picture = ImageCms.profileToProfile(picture, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                               ImageCms.createProfile("sRGB"), outputMode="RGB")
        if alpha is not None:
            picture.putalpha(alpha)
        picture.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        picture.save(destination, format="PNG")
    return destination


class AssetLoader:
    def __init__(self, cache_directory):
        self.cache = Path(cache_directory)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gallery-assets")
        self.closed = False
        self.pending = {}

    def _submit(self, key, work, callback):
        if self.closed:
            return
        if key in self.pending:
            self.pending[key].append(callback)
            return
        self.pending[key] = [callback]
        future = self.pool.submit(work)

        def finished(result):
            try:
                value, error = result.result(), None
            except Exception as exception:
                value, error = None, exception
            GLib.idle_add(self._deliver, key, value, error)

        future.add_done_callback(finished)

    def _deliver(self, key, value, error):
        callbacks = self.pending.pop(key, [])
        if not self.closed:
            for callback in callbacks:
                callback(value, error)
        return GLib.SOURCE_REMOVE

    def photo(self, path, callback):
        def prepare():
            source = Path(path)
            info = source.stat()
            key = hashlib.sha256(f"{source}:{info.st_size}:{info.st_mtime_ns}:1024:v1".encode()).hexdigest()
            destination = self.cache / f"photo-{key}.png"
            if not destination.exists():
                upright_thumbnail(source, destination)
            return destination

        self._submit(("photo", path), prepare, callback)

    def artwork(self, uri, callback):
        def prepare():
            parsed = urllib.parse.urlparse(uri)
            key = hashlib.sha256(uri.encode()).hexdigest()
            destination = self.cache / f"art-{key}.png"
            if destination.exists():
                return destination
            if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
                return upright_thumbnail(Path(urllib.parse.unquote(parsed.path)), destination, 600)
            if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username:
                raise ValueError("Spotify supplied an unsupported artwork address.")
            request = urllib.request.Request(uri, headers={"User-Agent": "GalleryDesk/1.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("Album artwork is too large.")
            return upright_thumbnail(io.BytesIO(data), destination, 600)

        self._submit(("art", uri), prepare, callback)

    def scan(self, directory, callback):
        self._submit(("scan", directory), lambda: scan_photos(directory), callback)

    def close(self):
        self.closed = True
        self.pending.clear()
        self.pool.shutdown(wait=False, cancel_futures=True)


def pixbuf(path):
    return GdkPixbuf.Pixbuf.new_from_file(str(path))
