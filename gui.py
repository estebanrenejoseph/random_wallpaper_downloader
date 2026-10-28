# gui.py

import mimetypes
import re
import shlex
import threading
from pathlib import Path
from urllib.parse import urlparse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib, GdkPixbuf, Gtk, Gdk

from models import ImageData
from network import http_get_bytes
from sources import SOURCES, WALLHAVEN_PURITY

APP_NAME = "Random Wallpaper Viewer"

def pixbuf_from_bytes(image_bytes):
    """
    Converts raw image bytes into a GdkPixbuf object.
    GdkPixbuf is the standard image representation in GTK for rendering.
    """
    loader = GdkPixbuf.PixbufLoader.new()
    try:
        loader.write(image_bytes)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            raise RuntimeError("GTK was unable to decode the image data.")
        return pixbuf.copy()
    finally:
        try:
            loader.close()
        except:
            pass

def fit_pixbuf(pixbuf, max_width=1400, max_height=850):
    """
    Scales the image to fit within the specified maximum dimensions while
    maintaining the original aspect ratio. Uses bilinear interpolation for
    better downscaling quality.
    """
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if width <= max_width and height <= max_height:
        return pixbuf

    scale = min(max_width / width, max_height / height)
    return pixbuf.scale_simple(
        max(1, int(width * scale)),
        max(1, int(height * scale)),
        GdkPixbuf.InterpType.BILINEAR
    )


class ImageViewer:
    def __init__(
        self,
        source,
        initial_tags=None,
        initial_content="sfw",
        initial_purity="sfw",
        initial_rating="safe",
        initial_categories=None,
        auto_reload=False,
        timer_seconds=30,
    ):
        # Application state initialization
        self.source = source
        self.current_image = None
        self.current_pixbuf = None
        self.original_pixbuf = None

        # Concurrency and UI state flags
        self.is_fullscreen = False
        self.closed = False
        self.worker_running = False
        self.reload_source_id = None
        self.next_request_id = 0
        self.active_request_id = 0
        self.pending_reload = False
        self.last_options = {
            "content": initial_content,
            "purity": initial_purity,
            "rating": initial_rating,
            "categories": list(initial_categories or []),
        }

        # Main window setup
        self.window = Gtk.Window(title=f"{APP_NAME} - {SOURCES[source].name}")
        self.window.set_default_size(1100, 800)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", self.on_destroy)
        self.window.connect("key-press-event", self.on_key_press)
        self.window.connect("configure-event", self.on_window_configure)

        # Root container (vertical layout)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.set_border_width(8)
        self.root = root
        self.window.add(root)

        # -------------------------------------------------------
        # Top controls: source selector & reload/autoreload UI
        # -------------------------------------------------------
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.top_controls = top
        root.pack_start(top, False, False, 0)

        top.pack_start(Gtk.Label(label="Source:"), False, False, 0)

        self.source_combo = Gtk.ComboBoxText()
        for key, spec in SOURCES.items():
            self.source_combo.append(key, spec.name)
        self.source_combo.set_active_id(source)
        self.source_combo.connect("changed", self.on_source_changed)
        top.pack_start(self.source_combo, False, False, 0)

        self.reload_button = Gtk.Button(label="Reload")
        self.reload_button.connect("clicked", self.on_reload_clicked)
        top.pack_start(self.reload_button, False, False, 0)

        self.fullscreen_button = Gtk.Button(label="Fullscreen")
        self.fullscreen_button.connect("clicked", self.on_fullscreen_clicked)
        top.pack_start(self.fullscreen_button, False, False, 0)

        self.auto_check = Gtk.CheckButton(label="Autoreload")
        self.auto_check.set_active(auto_reload)
        self.auto_check.connect("toggled", self.on_autoreload_toggled)
        top.pack_start(self.auto_check, False, False, 0)

        top.pack_start(Gtk.Label(label="Timer (s):"), False, False, 0)

        adjustment = Gtk.Adjustment(
            value=max(1, timer_seconds),
            lower=1,
            upper=86400,
            step_increment=1,
            page_increment=10,
            page_size=0,
        )
        self.timer_spin = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=0)
        self.timer_spin.connect("value-changed", self.on_timer_changed)
        top.pack_start(self.timer_spin, False, False, 0)

        self.download_button = Gtk.Button(label="Download")
        self.download_button.set_sensitive(False)
        self.download_button.connect("clicked", self.on_download_clicked)
        top.pack_end(self.download_button, False, False, 0)

        # -------------------------------------------------------
        # Source-specific filters frame
        # -------------------------------------------------------
        self.options_frame = Gtk.Frame(label="Source Options")
        root.pack_start(self.options_frame, False, False, 0)

        self.options_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
        )
        self.options_box.set_border_width(8)
        self.options_frame.add(self.options_box)

        # -------------------------------------------------------
        # Main content area (Notebook for tabs)
        # -------------------------------------------------------
        self.notebook = Gtk.Notebook()
        root.pack_start(self.notebook, True, True, 0)

        self.image_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        self.image_page.set_border_width(4)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        self.scrolled.add(self.image_page)
        self.notebook.append_page(
            self.scrolled,
            Gtk.Label(label="Image"),
        )

        self.image = Gtk.Image()
        self.image.set_halign(Gtk.Align.CENTER)
        self.image.set_valign(Gtk.Align.CENTER)
        self.image_page.pack_start(self.image, True, True, 0)

        self.status = Gtk.Label(label="Ready.")
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        self.image_page.pack_start(self.status, False, False, 0)

        # -------------------------------------------------------
        # Tags tab (dynamically inserted/removed based on source)
        # -------------------------------------------------------
        self.tags_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
        )
        self.tags_page.set_border_width(12)

        tag_hint = Gtk.Label(
            label="Search tags. Use quotes for a tag containing spaces."
        )
        tag_hint.set_xalign(0)
        self.tags_page.pack_start(tag_hint, False, False, 0)

        self.tags_entry = Gtk.Entry()
        self.tags_entry.set_placeholder_text(
            'e.g., 1girl blue_eyes   |   "gothic lolita"'
        )
        if initial_tags:
            self.tags_entry.set_text(shlex.join(initial_tags))
        self.tags_entry.connect("activate", self.on_tags_activate)
        self.tags_page.pack_start(self.tags_entry, False, False, 0)

        tag_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        self.tags_page.pack_start(tag_actions, False, False, 0)

        self.tags_reload_button = Gtk.Button(label="Apply and Reload")
        self.tags_reload_button.connect(
            "clicked", self.on_reload_clicked
        )
        tag_actions.pack_start(
            self.tags_reload_button, False, False, 0
        )

        self.tags_clear_button = Gtk.Button(label="Clear")
        self.tags_clear_button.connect("clicked", self.on_tags_clear)
        tag_actions.pack_start(
            self.tags_clear_button, False, False, 0
        )

        self.tags_help = Gtk.Label()
        self.tags_help.set_xalign(0)
        self.tags_help.set_line_wrap(True)
        self.tags_page.pack_start(
            self.tags_help, False, False, 0
        )

        self.tags_page_position = -1

        # -------------------------------------------------------
        # Initial GUI state rendering
        # -------------------------------------------------------
        self.rebuild_source_options(reset=False)
        self.update_tags_tab()

        self.window.show_all()
        self.update_tags_tab()
        self.reload()

        if auto_reload:
            self.restart_autoreload_timer()

    # -----------------------------------------------------------
    # Source / Option Handling
    # -----------------------------------------------------------

    def on_source_changed(self, combo):
        source = combo.get_active_id()
        if not source or source == self.source:
            return

        self.source = source
        self.window.set_title(
            f"{APP_NAME} - {SOURCES[source].name}"
        )
        self.rebuild_source_options(reset=True)
        self.update_tags_tab()

        # If an active request is running for the previous source, invalidate it.
        # Its result will be dropped in _fetch_success/_fetch_error, and a
        # new request will automatically trigger.
        if self.worker_running:
            self.pending_reload = True
            self.next_request_id += 1
            self.active_request_id = self.next_request_id
        else:
            self.reload()

    def clear_box(self, box):
        for child in box.get_children():
            box.remove(child)

    def add_labeled_combo(self, label_text, values, active_id):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label=label_text)
        box.pack_start(label, False, False, 0)

        combo = Gtk.ComboBoxText()
        for item_id, item_label in values:
            combo.append(item_id, item_label)

        if active_id not in [item_id for item_id, _ in values]:
            active_id = values[0][0]
        combo.set_active_id(active_id)
        combo.connect("changed", self.on_filter_control_changed)
        box.pack_start(combo, False, False, 0)
        self.options_box.pack_start(box, False, False, 0)
        return combo

    def rebuild_source_options(self, reset=True):
        self.clear_box(self.options_box)

        # Reset options to their defaults on source change.
        # Initial load keeps CLI arguments (reset=False).
        if reset:
            self.last_options["content"] = "sfw"
            self.last_options["purity"] = "sfw"
            self.last_options["rating"] = "safe"
            self.last_options["categories"] = []

        if self.source == "nasa":
            label = Gtk.Label(
                label="No filter options available for NASA APOD."
            )
            label.set_xalign(0)
            self.options_box.pack_start(label, False, False, 0)

        elif self.source == "waifu":
            self.content_combo = self.add_labeled_combo(
                "Content:",
                [
                    ("sfw", "SFW"),
                    ("nsfw", "NSFW"),
                    ("all", "All"),
                ],
                self.last_options["content"],
            )

        elif self.source == "wallhaven":
            self.purity_combo = self.add_labeled_combo(
                "Purity:",
                [(key, key.upper()) for key in WALLHAVEN_PURITY],
                self.last_options["purity"],
            )

            categories_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            categories_box.pack_start(
                Gtk.Label(label="Categories:"),
                False,
                False,
                0,
            )
            self.category_checks = {}
            for name, label in (
                ("general", "General"),
                ("anime", "Anime"),
                ("people", "People"),
            ):
                check = Gtk.CheckButton(label=label)
                if name in self.last_options.get("categories", []):
                    check.set_active(True)
                check.connect("toggled", self.on_filter_control_changed)
                self.category_checks[name] = check
                categories_box.pack_start(check, False, False, 0)
            self.options_box.pack_start(
                categories_box, False, False, 0
            )

        elif self.source == "danbooru":
            self.rating_combo = self.add_labeled_combo(
                "Rating:",
                [
                    ("safe", "Safe"),
                    ("questionable", "Questionable"),
                    ("explicit", "Explicit"),
                    ("all", "All"),
                ],
                self.last_options["rating"],
            )

        elif self.source == "nekos":
            self.content_combo = self.add_labeled_combo(
                "Content:",
                [
                    ("sfw", "SFW"),
                    ("nsfw", "NSFW"),
                ],
                self.last_options["content"],
            )

        self.options_box.show_all()
        self.update_tags_help()

    def update_tags_tab(self):
        supports_tags = SOURCES[self.source].supports_tags
        page_num = self.notebook.page_num(self.tags_page)

        if supports_tags and page_num < 0:
            self.tags_page_position = self.notebook.append_page(
                self.tags_page,
                Gtk.Label(label="Tags"),
            )
            self.notebook.show_all()
        elif not supports_tags and page_num >= 0:
            self.notebook.remove_page(page_num)
            self.tags_page_position = -1
            self.notebook.set_current_page(0)

        self.update_tags_help()

    def update_tags_help(self):
        if not SOURCES[self.source].supports_tags:
            return

        messages = {
            "wallhaven": (
                "Wallhaven: Multiple tags supported. "
                "Purity combinations can be used (e.g., SFW, Sketchy, or SFW+Sketchy)."
            ),
            "danbooru": (
                "Danbooru: Tags are appended to the query parameters. "
                "Maximum of two user tags allowed."
            ),
            "nekos": (
                "Nekos.moe: Tags are sent as a list. "
                'For multi-word tags, ensure you use quotation marks.'
            ),
        }
        self.tags_help.set_text(messages.get(self.source, ""))

    # -----------------------------------------------------------
    # Filter Retrieval
    # -----------------------------------------------------------

    def get_tags(self):
        """
        Parses tags securely via shlex to support multi-word strings.
        Quotation marks group words in the UI but are stripped before
        the API request.
        """
        text = self.tags_entry.get_text().strip()
        if not text:
            return []

        try:
            tags = shlex.split(text, comments=False, posix=True)
        except ValueError as exc:
            raise ValueError(
                "Invalid tag syntax, verify quotation marks. "
                f"({exc})"
            ) from exc

        return [tag.strip() for tag in tags if tag.strip()]

    def get_filters(self):
        content = self.content_combo.get_active_id() if hasattr(self, "content_combo") and self.source in {"waifu", "nekos"} else "sfw"
        purity = self.purity_combo.get_active_id() if hasattr(self, "purity_combo") and self.source == "wallhaven" else "sfw"
        rating = self.rating_combo.get_active_id() if hasattr(self, "rating_combo") and self.source == "danbooru" else "safe"
        categories = []
        if self.source == "wallhaven" and hasattr(self, "category_checks"):
            categories = [
                name for name, check in self.category_checks.items()
                if check.get_active()
            ]
        return self.get_tags(), content, purity, rating, categories

    def on_filter_control_changed(self, _widget):
        # Trigger an immediate image reload when a dropdown/checkbox filter changes.
        if not self.closed and not self.worker_running:
            self.reload()

    def on_tags_activate(self, _entry):
        self.reload()

    def on_tags_clear(self, _button):
        self.tags_entry.set_text("")
        self.reload()

    # -----------------------------------------------------------
    # Asynchronous Data Loading
    # -----------------------------------------------------------

    def on_reload_clicked(self, _button):
        self.reload()

    def reload(self):
        """
        Initiates the image fetch protocol.
        Network operations block execution, so they are offloaded to a separate
        daemon thread to keep the GTK main loop responsive.
        """
        if self.closed or self.worker_running:
            return

        self.worker_running = True
        self.reload_button.set_sensitive(False)
        self.download_button.set_sensitive(False)
        self.status.set_text(
            f"Loading from {SOURCES[self.source].name}..."
        )

        self.next_request_id += 1
        request_id = self.next_request_id
        self.active_request_id = request_id

        tags, content, purity, rating, categories = self.get_filters()

        # Threading is critical here for application responsiveness
        thread = threading.Thread(
            target=self._fetch_worker,
            args=(
                request_id,
                self.source,
                tags,
                content,
                purity,
                rating,
                categories,
            ),
            daemon=True,
        )
        thread.start()

    def _fetch_worker(
        self,
        request_id,
        source,
        tags,
        content,
        purity,
        rating,
        categories,
    ):
        """
        Background worker handling HTTP requests.
        UI updates from background threads cause GTK crashes. GLib.idle_add
        queues the success/error callback to execute safely on the main thread.
        """
        try:
            spec = SOURCES[source]
            image_url, title, metadata = spec.function(
                tags=tags,
                content=content,
                purity=purity,
                rating=rating,
                categories=categories,
            )

            image_bytes, content_type = http_get_bytes(image_url)

            image = ImageData(
                data=image_bytes,
                url=image_url,
                source=source,
                title=title,
                content_type=content_type,
                metadata=metadata,
            )

            # Delegate UI updates back to the GTK main loop
            GLib.idle_add(
                self._fetch_success,
                request_id,
                image,
            )

        except Exception as exc:
            GLib.idle_add(
                self._fetch_error,
                request_id,
                source,
                str(exc),
            )

    def _fetch_success(self, request_id, image):
        if self.closed:
            return False

        # Guard clause handling race conditions when rapidly changing sources.
        if request_id != self.active_request_id:
            self.worker_running = False
            if self.pending_reload and not self.closed:
                self.pending_reload = False
                GLib.idle_add(self.reload)
            return False

        try:
            pixbuf = pixbuf_from_bytes(image.data)

            self.current_image = image
            self.original_pixbuf = pixbuf
            self.update_display_pixbuf()

            details = []
            metadata = image.metadata

            if metadata.get("rating"):
                details.append(f"rating={metadata['rating']}")
            if metadata.get("purity"):
                details.append(f"purity={metadata['purity']}")
            if metadata.get("is_nsfw") is not None:
                details.append(f"nsfw={metadata['is_nsfw']}")
            if metadata.get("nsfw") is not None:
                details.append(f"nsfw={metadata['nsfw']}")
            if metadata.get("width") and metadata.get("height"):
                details.append(
                    f"{metadata['width']}×{metadata['height']}"
                )
            if metadata.get("resolution"):
                w, h = metadata["resolution"]
                if w and h:
                    details.append(f"{w}×{h}")

            line = f"{SOURCES[image.source].name} — {image.title}"
            if details:
                line += " — " + " | ".join(details)

            self.status.set_text(line)
            self.worker_running = False
            self.reload_button.set_sensitive(True)
            self.download_button.set_sensitive(True)

        except Exception as exc:
            self.current_image = None
            self.current_pixbuf = None
            self.original_pixbuf = None
            self.image.clear()
            self.worker_running = False
            self.reload_button.set_sensitive(True)
            self.download_button.set_sensitive(False)
            self.status.set_text(
                f"Decoding error: {exc}"
            )

        return False

    def _fetch_error(self, request_id, source, message):
        if self.closed:
            return False

        if request_id != self.active_request_id:
            self.worker_running = False
            if self.pending_reload and not self.closed:
                self.pending_reload = False
                GLib.idle_add(self.reload)
            return False

        self.current_image = None
        self.current_pixbuf = None
        self.original_pixbuf = None
        self.image.clear()
        self.worker_running = False
        self.reload_button.set_sensitive(True)
        self.download_button.set_sensitive(False)
        self.status.set_text(
            f"Error {SOURCES[source].name}: {message}"
        )
        return False

    def update_display_pixbuf(self):
        """
        Dynamically renders the image relying on window dimensions.
        In fullscreen mode, the allocation area expands natively without
        interrupting the background autoreload loops.
        """
        if self.original_pixbuf is None:
            return

        allocation = self.scrolled.get_allocation()
        width = allocation.width
        height = allocation.height

        if width <= 50 or height <= 50:
            width = 1400
            height = 850

        # Preserve a margin to avoid triggering layout loop recursions
        width = max(100, width - 16)
        height = max(100, height - 16)

        pixbuf = fit_pixbuf(
            self.original_pixbuf,
            max_width=width,
            max_height=height,
        )
        self.current_pixbuf = pixbuf
        self.image.set_from_pixbuf(pixbuf)

    def on_fullscreen_clicked(self, _button):
        self.toggle_fullscreen()

    def toggle_fullscreen(self):
        """Toggles fullscreen state without halting background tasks."""
        if self.closed:
            return

        self.is_fullscreen = not self.is_fullscreen

        if self.is_fullscreen:
            # Hide the UI chrome to prioritize the image content
            self.top_controls.hide()
            self.options_frame.hide()
            self.notebook.set_show_tabs(False)
            self.status.hide()
            self.window.fullscreen()
            self.fullscreen_button.set_label("Exit Fullscreen")
        else:
            self.window.unfullscreen()
            self.notebook.set_show_tabs(True)
            self.top_controls.show()
            self.options_frame.show()
            self.status.show()
            self.fullscreen_button.set_label("Fullscreen")

        GLib.idle_add(self.update_display_pixbuf)

    def on_key_press(self, widget, event):
        """
        Keyboard shortcut listener.
        Note: The method signature requires 'self, widget, event' to properly
        bind to a GTK instance callback.

        F11   : Toggle fullscreen
        Escape: Exit fullscreen
        """
        key = Gdk.keyval_name(event.keyval)

        if key == "F11":
            if self.window.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                self.window.unfullscreen()
            else:
                self.window.fullscreen()
            return True

        if key == "Escape":
            if self.window.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                self.window.unfullscreen()
                return True

        return False

    def on_window_configure(self, _widget, _event):
        if self.current_image and not self.closed:
            GLib.idle_add(self.update_display_pixbuf)
        return False

    # -----------------------------------------------------------
    # Autoreload Timer Management
    # -----------------------------------------------------------

    def on_autoreload_toggled(self, _button):
        self.restart_autoreload_timer()
        if self.auto_check.get_active():
            self.status.set_text(
                f"Autoreload enabled — every "
                f"{int(self.timer_spin.get_value())} s."
            )

    def on_timer_changed(self, _spin):
        if self.auto_check.get_active():
            self.restart_autoreload_timer()

    def restart_autoreload_timer(self):
        if self.reload_source_id is not None:
            GLib.source_remove(self.reload_source_id)
            self.reload_source_id = None

        if self.auto_check.get_active() and not self.closed:
            seconds = max(1, int(self.timer_spin.get_value()))
            self.reload_source_id = GLib.timeout_add_seconds(
                seconds,
                self._autoreload_tick,
            )

    def _autoreload_tick(self):
        self.reload_source_id = None

        if self.closed or not self.auto_check.get_active():
            return False

        # Drop execution if the previous fetch thread is still active.
        if not self.worker_running:
            self.reload()

        seconds = max(1, int(self.timer_spin.get_value()))
        self.reload_source_id = GLib.timeout_add_seconds(
            seconds,
            self._autoreload_tick,
        )
        return False

    # -----------------------------------------------------------
    # Download Handling
    # -----------------------------------------------------------

    def suggest_filename(self):
        if not self.current_image:
            return "image.jpg"

        title = (
            self.current_image.title
            or self.current_image.source
            or "image"
        )
        title = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            title,
        ).strip("._") or "image"

        parsed = urlparse(self.current_image.url)
        suffix = Path(parsed.path).suffix.lower()

        allowed = {
            ".jpg", ".jpeg", ".png", ".gif", ".webp",
            ".bmp", ".avif", ".tif", ".tiff"
        }

        if suffix not in allowed:
            suffix = (
                mimetypes.guess_extension(
                    self.current_image.content_type
                )
                or ".jpg"
            )

        return f"{title}{suffix}"

    def on_download_clicked(self, _button):
        if not self.current_image:
            return

        dialog = Gtk.FileChooserDialog(
            title="Save Image",
            parent=self.window,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE,
            Gtk.ResponseType.ACCEPT,
        )
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(self.suggest_filename())

        response = dialog.run()
        path = (
            dialog.get_filename()
            if response == Gtk.ResponseType.ACCEPT
            else None
        )
        dialog.destroy()

        if not path:
            return

        try:
            with open(path, "wb") as file:
                file.write(self.current_image.data)
            self.status.set_text(
                f"Image saved: {path}"
            )
        except OSError as exc:
            self.status.set_text(
                f"Save error: {exc}"
            )

    # -----------------------------------------------------------
    # Application Teardown
    # -----------------------------------------------------------

    def on_destroy(self, _window):
        self.closed = True

        if self.reload_source_id is not None:
            GLib.source_remove(self.reload_source_id)
            self.reload_source_id = None

        self.current_image = None
        self.current_pixbuf = None
        self.original_pixbuf = None
        self.active_request_id = -1

        # Kill the main event loop, allowing the process to gracefully exit
        Gtk.main_quit()
