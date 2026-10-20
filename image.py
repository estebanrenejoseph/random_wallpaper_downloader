#!/usr/bin/env python3

"""
image_v5.py - multi-source image viewer

Architecture :
    API/source -> bytes en RAM -> GdkPixbuf -> GTK

Aucun fichier image temporaire n'est créé.
Le bouton « Télécharger » écrit le fichier uniquement à la demande.

Sources :
    nasa
    waifu
    wallhaven
    danbooru
    nekos


python python-requests python-gobject gtk3 gdk-pixbuf2
"""

import argparse
import mimetypes
import os
import re
import shlex
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, GdkPixbuf, Gtk


HTTP_TIMEOUT = 30
APP_NAME = "Image Viewer v5"
USER_AGENT = "image-viewer-v5/1.0 (local desktop application)"


@dataclass
class ImageData:
    data: bytes
    url: str
    source: str
    title: str = ""
    content_type: str = "application/octet-stream"
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SourceSpec:
    name: str
    function: object
    supports_tags: bool
    description: str


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_get_json(url, params=None, headers=None):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    response = requests.get(
        url,
        params=params,
        headers=h,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"La réponse de {url} n'est pas un JSON valide "
            f"(HTTP {response.status_code})."
        ) from exc


def http_post_json(url, payload=None, headers=None):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    response = requests.post(
        url,
        json=payload or {},
        headers=h,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"La réponse de {url} n'est pas un JSON valide "
            f"(HTTP {response.status_code})."
        ) from exc


def http_get_bytes(url, headers=None):
    """Download image bytes in RAM. Never writes an image to disk."""
    h = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    if headers:
        h.update(headers)
    response = requests.get(
        url,
        headers=h,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    content = response.content
    if not content:
        raise RuntimeError("Le téléchargement de l'image est vide.")
    content_type = response.headers.get(
        "Content-Type", "application/octet-stream"
    ).split(";", 1)[0].lower()
    return content, content_type


# ---------------------------------------------------------------------------
# Source NASA
# ---------------------------------------------------------------------------

def source_nasa(tags=None, content="sfw", purity="sfw", rating="safe", categories=None):
    if tags or content != "sfw" or purity != "sfw" or rating != "safe" or categories:
        raise ValueError("NASA APOD ne propose pas ces filtres.")

    data = http_get_json(
        "https://science.nasa.gov/wp-json/wp/v2/apod-basic",
        params={"api_key": "DEMO_KEY"},
    )
    if isinstance(data, list):
        if not data:
            raise RuntimeError("NASA n'a retourné aucune donnée.")
        item = data[0]
    elif isinstance(data, dict):
        item = data
    else:
        raise RuntimeError("Format de réponse NASA inattendu.")

    if item.get("media_type") != "image":
        raise RuntimeError("L'APOD du jour n'est pas une image.")

    image_url = item.get("hdurl") or item.get("url")
    if not image_url or not image_url.startswith(("http://", "https://")):
        raise RuntimeError("NASA n'a pas fourni une URL d'image exploitable.")

    return image_url, item.get("title", "NASA APOD"), {
        "date": item.get("date"),
        "copyright": item.get("copyright"),
    }


# ---------------------------------------------------------------------------
# Source Waifu.im
# ---------------------------------------------------------------------------

WAIFU_CONTENT = {
    "sfw": "False",
    "nsfw": "True",
    "all": "All",
}


def source_waifu(tags=None, content="sfw", purity="sfw", rating="safe", categories=None):
    if content not in WAIFU_CONTENT:
        raise ValueError("Mode de contenu Waifu.im invalide.")
    if purity != "sfw" or rating != "safe" or categories:
        raise ValueError("Pour Waifu.im, utilisez --content et les tags.")

    params = [("IsNsfw", WAIFU_CONTENT[content])]
    for tag in tags or []:
        params.append(("IncludedTags", tag))

    data = http_get_json(
        "https://api.waifu.im/images",
        params=params,
        headers={"Accept-Version": "v7"},
    )
    items = data.get("items", [])
    if not items:
        raise RuntimeError("Waifu.im n'a trouvé aucune image pour ces filtres.")

    # L'API peut retourner plusieurs candidats.
    import random
    item = random.choice(items)
    image_url = item.get("url")
    if not image_url:
        raise RuntimeError("Waifu.im n'a pas fourni d'URL d'image.")

    return image_url, f"Waifu.im #{item.get('id', '?')}", {
        "is_nsfw": item.get("isNsfw"),
        "width": item.get("width"),
        "height": item.get("height"),
        "tags": item.get("tags", []),
    }


def list_waifu_tags():
    data = http_get_json(
        "https://api.waifu.im/tags",
        headers={"Accept-Version": "v7"},
    )
    if isinstance(data, dict):
        values = []
        for value in data.values():
            values.extend(value if isinstance(value, list) else [value])
    elif isinstance(data, list):
        values = data
    else:
        values = [data]

    for value in values:
        if isinstance(value, dict):
            print(value.get("slug") or value.get("name") or value)
        else:
            print(value)


# ---------------------------------------------------------------------------
# Source Wallhaven
# ---------------------------------------------------------------------------

WALLHAVEN_PURITY = {
    # V5 : NSFW volontairement retiré de l'interface.
    # Modes disponibles : SFW, Sketchy, ou les deux.
    "sfw": "100",
    "sketchy": "010",
    "sfw+sketchy": "110",
}



def _wallhaven_tag_query(tags):
    """
    Construit le paramètre q de Wallhaven à partir des tags.

    Un tag simple devient +tag.
    Un tag contenant des espaces reste un seul bloc grâce aux guillemets.

    Exemples :
        ["manga"] -> +manga
        ["manga", "anime"] -> +manga +anime
        ["gothic lolita"] -> +"gothic lolita"
    """
    parts = []

    for tag in tags or []:
        clean = tag.strip()
        if not clean:
            continue

        if any(ch.isspace() for ch in clean):
            escaped = clean.replace('"', '\\"')
            parts.append(f'+"{escaped}"')
        else:
            parts.append(f'+{clean}')

    return " ".join(parts)


def source_wallhaven(tags=None, content="sfw", purity="sfw", rating="safe", categories=None):
    if purity not in WALLHAVEN_PURITY:
        raise ValueError("Purity Wallhaven invalide.")
    if content != "sfw" or rating != "safe":
        raise ValueError("Pour Wallhaven, utilisez --purity, --category et les tags.")

    if categories:
        wanted = set(categories)
        category_value = "".join(
            "1" if name in wanted else "0"
            for name in ("general", "anime", "people")
        )
        if category_value == "000":
            raise ValueError("Aucune catégorie Wallhaven valide.")
    else:
        category_value = "111"

    params = {
        "categories": category_value,
        "purity": WALLHAVEN_PURITY[purity],
        "sorting": "random",
        "page": 1,
    }

    if tags:
        params["q"] = _wallhaven_tag_query(tags)

    api_key = os.getenv("WALLHAVEN_API_KEY")
    if api_key:
        params["apikey"] = api_key

    data = http_get_json(
        "https://wallhaven.cc/api/v1/search",
        params=params,
    )
    results = data.get("data", [])
    if not results:
        raise RuntimeError(
            "Wallhaven n'a trouvé aucune image pour ces filtres. "
            "Vérifiez les tags, la purity, les catégories et éventuellement la clé API."
        )

    import random
    item = random.choice(results)
    image_url = item.get("path")
    if not image_url:
        raise RuntimeError("Wallhaven n'a pas fourni d'URL d'image.")

    return image_url, f"Wallhaven #{item.get('id', '?')}", {
        "resolution": (item.get("dimension_x"), item.get("dimension_y")),
        "category": item.get("category"),
        "purity": item.get("purity"),
        "tags": item.get("tags", []),
    }


# ---------------------------------------------------------------------------
# Source Danbooru
# ---------------------------------------------------------------------------

DANBOORU_RATING = {
    "safe": "s",
    "questionable": "q",
    "explicit": "e",
}


def source_danbooru(tags=None, content="sfw", purity="sfw", rating="safe", categories=None):
    if content != "sfw" or purity != "sfw" or categories:
        raise ValueError("Pour Danbooru, utilisez --rating et les tags.")
    if rating not in {"safe", "questionable", "explicit", "all"}:
        raise ValueError("Rating Danbooru invalide.")

    user_tags = [tag.strip() for tag in (tags or []) if tag.strip()]
    # Danbooru impose des limites sur les recherches anonymes.
    if len(user_tags) > 2:
        raise ValueError(
            "Danbooru limite les recherches anonymes à 2 tags utilisateur. "
            "Utilisez au maximum deux tags."
        )

    query_parts = list(user_tags)
    if rating != "all":
        query_parts.append(f"rating:{DANBOORU_RATING[rating]}")

    # Important : random=true est un paramètre HTTP, pas un tag Danbooru.
    params = {
        "tags": " ".join(query_parts),
        "limit": 1,
        "random": "true",
    }

    data = http_get_json(
        "https://danbooru.donmai.us/posts.json",
        params=params,
        headers={
            "User-Agent": "image-viewer-v5/1.0 (Danbooru client)",
            "Accept": "application/json",
        },
    )

    posts = data.get("posts", []) if isinstance(data, dict) else data
    if not posts:
        raise RuntimeError("Danbooru n'a trouvé aucune image pour ces filtres.")

    post = posts[0]
    image_url = (
        post.get("file_url")
        or post.get("large_file_url")
        or post.get("preview_file_url")
    )

    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url
    elif image_url and image_url.startswith("/"):
        image_url = "https://danbooru.donmai.us" + image_url

    if not image_url:
        raise RuntimeError(
            "Danbooru a retourné le post mais aucune URL d'image exploitable."
        )

    return image_url, f"Danbooru #{post.get('id', '?')}", {
        "rating": post.get("rating"),
        "score": post.get("score"),
        "width": post.get("image_width"),
        "height": post.get("image_height"),
        "tags": post.get("tag_string", ""),
    }


# ---------------------------------------------------------------------------
# Source Nekos.moe
# ---------------------------------------------------------------------------

NEKOS_CONTENT = {
    "sfw": False,
    "nsfw": True,
}


def _nekos_image_url(item):
    """Build a public file URL from a Nekos.moe image object."""
    direct = (
        item.get("image_url")
        or item.get("url")
        or item.get("file_url")
    )
    if direct and direct.startswith(("http://", "https://")):
        return direct

    image_id = item.get("id")
    if not image_id:
        raise RuntimeError("Nekos.moe n'a pas fourni d'identifiant d'image.")
    return f"https://nekos.moe/image/{image_id}.jpg"


def source_nekos(tags=None, content="sfw", purity="sfw", rating="safe", categories=None):
    if content not in NEKOS_CONTENT:
        raise ValueError("Nekos.moe propose ici les modes SFW ou NSFW.")
    if purity != "sfw" or rating != "safe" or categories:
        raise ValueError("Pour Nekos.moe, utilisez --content et les tags.")

    user_tags = [tag.strip() for tag in (tags or []) if tag.strip()]

    if user_tags:
        # L'API documente la recherche par POST /images/search,
        # avec tags comme tableau de chaînes.
        payload = {
            "nsfw": NEKOS_CONTENT[content],
            "tags": user_tags,
            "sort": "relevance",
            "skip": 0,
            "limit": 20,
        }
        data = http_post_json(
            "https://nekos.moe/api/v1/images/search",
            payload=payload,
        )
        items = data.get("images", []) if isinstance(data, dict) else []
    else:
        data = http_get_json(
            "https://nekos.moe/api/v1/random/image",
            params={
                "nsfw": str(NEKOS_CONTENT[content]).lower(),
                "count": 1,
            },
        )
        items = data.get("images", []) if isinstance(data, dict) else []

    if not items:
        raise RuntimeError(
            "Nekos.moe n'a trouvé aucune image pour ces filtres."
        )

    import random
    item = random.choice(items)
    image_url = _nekos_image_url(item)

    return image_url, f"Nekos.moe #{item.get('id', '?')}", {
        "nsfw": item.get("nsfw"),
        "tags": item.get("tags", []),
        "artist": item.get("artist"),
    }


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------

SOURCES = {
    "nasa": SourceSpec(
        "NASA",
        source_nasa,
        False,
        "NASA APOD — image du jour",
    ),
    "waifu": SourceSpec(
        "Waifu.im",
        source_waifu,
        True,
        "Images avec tags et filtre SFW/NSFW",
    ),
    "wallhaven": SourceSpec(
        "Wallhaven",
        source_wallhaven,
        True,
        "Wallpapers avec tags, catégories et SFW/Sketchy",
    ),
    "danbooru": SourceSpec(
        "Danbooru",
        source_danbooru,
        True,
        "Images avec tags et rating",
    ),
    "nekos": SourceSpec(
        "Nekos.moe",
        source_nekos,
        True,
        "Images avec tags et filtre SFW/NSFW",
    ),
}


# ---------------------------------------------------------------------------
# GTK image decoding
# ---------------------------------------------------------------------------

def pixbuf_from_bytes(image_bytes):
    loader = GdkPixbuf.PixbufLoader.new()
    try:
        loader.write(image_bytes)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            raise RuntimeError("GTK/GdkPixbuf n'a pas pu décoder l'image.")
        return pixbuf.copy()
    finally:
        try:
            loader.close()
        except Exception:
            pass


def fit_pixbuf(pixbuf, max_width=1400, max_height=850):
    width = pixbuf.get_width()
    height = pixbuf.get_height()

    if width <= max_width and height <= max_height:
        return pixbuf

    scale = min(max_width / width, max_height / height)
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))

    return pixbuf.scale_simple(
        new_width,
        new_height,
        GdkPixbuf.InterpType.BILINEAR,
    )


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------

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
        timer_seconds=60,
    ):
        self.source = source
        self.current_image = None
        self.current_pixbuf = None
        self.original_pixbuf = None
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

        self.window = Gtk.Window(title=f"{APP_NAME} - {SOURCES[source].name}")
        self.window.set_default_size(1100, 800)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("destroy", self.on_destroy)
        self.window.connect("key-press-event", self.on_key_press)
        self.window.connect("configure-event", self.on_window_configure)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        root.set_border_width(8)
        self.root = root
        self.window.add(root)

        # -------------------------------------------------------
        # Top controls : source selector + reload/autoreload
        # -------------------------------------------------------
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.top_controls = top
        root.pack_start(top, False, False, 0)

        top.pack_start(Gtk.Label(label="Source :"), False, False, 0)

        self.source_combo = Gtk.ComboBoxText()
        for key, spec in SOURCES.items():
            self.source_combo.append(key, spec.name)
        self.source_combo.set_active_id(source)
        self.source_combo.connect("changed", self.on_source_changed)
        top.pack_start(self.source_combo, False, False, 0)

        self.reload_button = Gtk.Button(label="Reload")
        self.reload_button.connect("clicked", self.on_reload_clicked)
        top.pack_start(self.reload_button, False, False, 0)

        self.fullscreen_button = Gtk.Button(label="Plein écran")
        self.fullscreen_button.connect("clicked", self.on_fullscreen_clicked)
        top.pack_start(self.fullscreen_button, False, False, 0)

        self.auto_check = Gtk.CheckButton(label="Autoreload")
        self.auto_check.set_active(auto_reload)
        self.auto_check.connect("toggled", self.on_autoreload_toggled)
        top.pack_start(self.auto_check, False, False, 0)

        top.pack_start(Gtk.Label(label="Timer (s) :"), False, False, 0)

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

        self.download_button = Gtk.Button(label="Télécharger")
        self.download_button.set_sensitive(False)
        self.download_button.connect("clicked", self.on_download_clicked)
        top.pack_end(self.download_button, False, False, 0)

        # -------------------------------------------------------
        # Source-specific filters
        # -------------------------------------------------------
        self.options_frame = Gtk.Frame(label="Options de la source")
        root.pack_start(self.options_frame, False, False, 0)

        self.options_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
        )
        self.options_box.set_border_width(8)
        self.options_frame.add(self.options_box)

        # -------------------------------------------------------
        # Notebook
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

        self.status = Gtk.Label(label="Prêt.")
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        self.image_page.pack_start(self.status, False, False, 0)

        # -------------------------------------------------------
        # Tags tab: created once but inserted/removed dynamically.
        # -------------------------------------------------------
        self.tags_page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
        )
        self.tags_page.set_border_width(12)

        tag_hint = Gtk.Label(
            label="Tags de recherche. Utilisez des guillemets pour un tag contenant des espaces."
        )
        tag_hint.set_xalign(0)
        self.tags_page.pack_start(tag_hint, False, False, 0)

        self.tags_entry = Gtk.Entry()
        self.tags_entry.set_placeholder_text(
            'ex. 1girl blue_eyes   |   "gothic lolita"'
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

        self.tags_reload_button = Gtk.Button(label="Appliquer et Reload")
        self.tags_reload_button.connect(
            "clicked", self.on_reload_clicked
        )
        tag_actions.pack_start(
            self.tags_reload_button, False, False, 0
        )

        self.tags_clear_button = Gtk.Button(label="Effacer")
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
        # Initial state
        # -------------------------------------------------------
        self.rebuild_source_options(reset=False)
        self.update_tags_tab()

        self.window.show_all()
        self.update_tags_tab()
        self.reload()

        if auto_reload:
            self.restart_autoreload_timer()

    # -----------------------------------------------------------
    # Source / option handling
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

        # Si une requête de l'ancienne source tourne encore, on l'invalide.
        # Son résultat sera ignoré et une nouvelle requête sera lancée
        # dès que l'ancien worker aura terminé.
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
        for item_id, label_text in values:
            combo.append(item_id, label_text)

        if active_id not in [item_id for item_id, _ in values]:
            active_id = values[0][0]
        combo.set_active_id(active_id)
        combo.connect("changed", self.on_filter_control_changed)
        box.pack_start(combo, False, False, 0)
        self.options_box.pack_start(box, False, False, 0)
        return combo

    def rebuild_source_options(self, reset=True):
        self.clear_box(self.options_box)

        # Lors d'un changement de source, on revient aux valeurs par défaut
        # de cette source. Lors de l'initialisation, on conserve les options CLI.
        if reset:
            self.last_options["content"] = "sfw"
            self.last_options["purity"] = "sfw"
            self.last_options["rating"] = "safe"
            self.last_options["categories"] = []

        if self.source == "nasa":
            label = Gtk.Label(
                label="Aucune option de filtre pour NASA APOD."
            )
            label.set_xalign(0)
            self.options_box.pack_start(label, False, False, 0)

        elif self.source == "waifu":
            self.content_combo = self.add_labeled_combo(
                "Contenu :",
                [
                    ("sfw", "SFW"),
                    ("nsfw", "NSFW"),
                    ("all", "Tout"),
                ],
                self.last_options["content"],
            )

        elif self.source == "wallhaven":
            self.purity_combo = self.add_labeled_combo(
                "Purity :",
                [(key, key.upper()) for key in WALLHAVEN_PURITY],
                self.last_options["purity"],
            )

            categories_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            categories_box.pack_start(
                Gtk.Label(label="Catégories :"),
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
                "Rating :",
                [
                    ("safe", "Safe"),
                    ("questionable", "Questionable"),
                    ("explicit", "Explicit"),
                    ("all", "Tout"),
                ],
                self.last_options["rating"],
            )

        elif self.source == "nekos":
            self.content_combo = self.add_labeled_combo(
                "Contenu :",
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
            "waifu": (
                "Waifu.im : chaque tag devient un paramètre IncludedTags. "
            ),
            "wallhaven": (
                "Wallhaven : plusieurs tags sont possibles. "
                "Purity disponible ici : SFW, Sketchy ou SFW+Sketchy."
            ),
            "danbooru": (
                "Danbooru : les tags sont envoyés dans le paramètre tags. "
                "Les tags utilisateur restent limités à deux."
            ),
            "nekos": (
                "Nekos.moe : les tags sont envoyés comme liste. "
                'Pour un tag avec espaces, utilisez des guillemets'
            ),
        }
        self.tags_help.set_text(messages.get(self.source, ""))

    # -----------------------------------------------------------
    # Current filters
    # -----------------------------------------------------------

    def get_tags(self):
        """
        Parse les tags en respectant les tags multi-mots.

        Les guillemets servent uniquement au champ de saisie et ne sont
        pas envoyés aux APIs.
        """
        text = self.tags_entry.get_text().strip()
        if not text:
            return []

        try:
            tags = shlex.split(text, comments=False, posix=True)
        except ValueError as exc:
            raise ValueError(
                "Syntaxe des tags invalide. Vérifiez les guillemets. "
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
        # Un changement de filtre recharge immédiatement l'image.
        # Pour les tags, le bouton/Entrée sert de validation explicite.
        if not self.closed and not self.worker_running:
            self.reload()

    def on_tags_activate(self, _entry):
        self.reload()

    def on_tags_clear(self, _button):
        self.tags_entry.set_text("")
        self.reload()

    # -----------------------------------------------------------
    # Loading
    # -----------------------------------------------------------

    def on_reload_clicked(self, _button):
        self.reload()

    def reload(self):
        if self.closed or self.worker_running:
            return

        self.worker_running = True
        self.reload_button.set_sensitive(False)
        self.download_button.set_sensitive(False)
        self.status.set_text(
            f"Chargement depuis {SOURCES[self.source].name}…"
        )

        self.next_request_id += 1
        request_id = self.next_request_id
        self.active_request_id = request_id

        tags, content, purity, rating, categories = self.get_filters()

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

        if request_id != self.active_request_id:
            # Résultat obsolète : on libère le worker puis on relance
            # éventuellement la requête demandée après un changement de source.
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
                f"Erreur de décodage : {exc}"
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
            f"Erreur {SOURCES[source].name} : {message}"
        )
        return False

    def update_display_pixbuf(self):
        """Render the original image to the current window size.

        In fullscreen, the available area is larger; autoreload continues
        independently because it is driven by the GTK timeout and worker
        thread, not by this rendering function.
        """
        if self.original_pixbuf is None:
            return

        allocation = self.scrolled.get_allocation()
        width = allocation.width
        height = allocation.height

        if width <= 50 or height <= 50:
            width = 1400
            height = 850

        # Reserve a small margin around the image.
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
        """Toggle image fullscreen without stopping autoreload."""
        if self.closed:
            return

        self.is_fullscreen = not self.is_fullscreen

        if self.is_fullscreen:
            # Keep the image page visible, but remove the UI chrome.
            self.top_controls.hide()
            self.options_frame.hide()
            self.notebook.set_show_tabs(False)
            self.status.hide()
            self.window.fullscreen()
            self.fullscreen_button.set_label("Quitter plein écran")
        else:
            self.window.unfullscreen()
            self.notebook.set_show_tabs(True)
            self.top_controls.show()
            self.options_frame.show()
            self.status.show()
            self.fullscreen_button.set_label("Plein écran")

        GLib.idle_add(self.update_display_pixbuf)

    def on_key_press(window, event):
        """
        Gestion des raccourcis clavier.

        F11   : entrer / sortir du plein écran
        Escape: quitter le plein écran
        """

        key = Gdk.keyval_name(event.keyval)

        if key == "F11":
            if window.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                window.unfullscreen()
            else:
                window.fullscreen()

            return True

        if key == "Escape":
            if window.get_window().get_state() & Gdk.WindowState.FULLSCREEN:
                window.unfullscreen()
                return True

        return False

    def on_window_configure(self, _widget, _event):
        if self.current_image and not self.closed:
            GLib.idle_add(self.update_display_pixbuf)
        return False

    # -----------------------------------------------------------
    # Autoreload
    # -----------------------------------------------------------

    def on_autoreload_toggled(self, _button):
        self.restart_autoreload_timer()
        if self.auto_check.get_active():
            self.status.set_text(
                f"Autoreload activé — toutes les "
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

        # Si un précédent chargement est encore en cours, on ne lance
        # pas une seconde requête. La prochaine échéance reprendra le relais.
        if not self.worker_running:
            self.reload()

        seconds = max(1, int(self.timer_spin.get_value()))
        self.reload_source_id = GLib.timeout_add_seconds(
            seconds,
            self._autoreload_tick,
        )
        return False

    # -----------------------------------------------------------
    # Download
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
            title="Enregistrer l'image",
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
                f"Image enregistrée : {path}"
            )
        except OSError as exc:
            self.status.set_text(
                f"Erreur d'enregistrement : {exc}"
            )

    # -----------------------------------------------------------
    # Close
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
        Gtk.main_quit()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Image Viewer v5"
        )
    )

    parser.add_argument(
        "source",
        nargs="?",
        default="nasa",
        choices=list(SOURCES.keys()),
        help="Source initiale (défaut : nasa)",
    )

    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help=(
            "Tag initial ; répétable. Utilisez des guillemets pour un tag "
            'contenant des espaces, par ex. --tag "gothic lolita".'
        ),
    )

    parser.add_argument(
        "--content",
        choices=("sfw", "nsfw", "all"),
        default="sfw",
        help="Waifu.im ; Nekos.moe accepte SFW/NSFW seulement",
    )

    parser.add_argument(
        "--purity",
        choices=tuple(WALLHAVEN_PURITY.keys()),
        default="sfw",
        help="Wallhaven : sfw, sketchy ou sfw+sketchy",
    )

    parser.add_argument(
        "--category",
        action="append",
        choices=("general", "anime", "people"),
        default=[],
        help="Wallhaven ; répétable",
    )

    parser.add_argument(
        "--rating",
        choices=("safe", "questionable", "explicit", "all"),
        default="safe",
        help="Danbooru",
    )

    parser.add_argument(
        "--list-tags",
        action="store_true",
        help="Lister les tags Waifu.im et quitter",
    )

    parser.add_argument(
        "--auto-reload",
        action="store_true",
        help="Activer l'autoreload au démarrage",
    )

    parser.add_argument(
        "--timer",
        type=int,
        default=60,
        help="Intervalle autoreload en secondes (défaut : 60)",
    )

    return parser


def validate_args(args):
    if args.timer < 1:
        raise ValueError("--timer doit être supérieur ou égal à 1 seconde.")

    if args.source == "nasa":
        if args.tag or args.category or args.content != "sfw" or args.purity != "sfw" or args.rating != "safe":
            raise ValueError("NASA n'accepte pas ces filtres.")

    elif args.source == "waifu":
        if args.category or args.purity != "sfw" or args.rating != "safe":
            raise ValueError("Waifu.im utilise --content et --tag.")

    elif args.source == "wallhaven":
        if args.content != "sfw" or args.rating != "safe":
            raise ValueError("Wallhaven utilise --purity, --category et --tag.")

    elif args.source == "danbooru":
        if args.content != "sfw" or args.purity != "sfw" or args.category:
            raise ValueError("Danbooru utilise --rating et --tag.")
        if len(args.tag) > 2:
            raise ValueError("Danbooru limite les recherches anonymes à 2 tags.")

    elif args.source == "nekos":
        if args.content == "all":
            raise ValueError(
                "Nekos.moe : le mode 'all' n'est pas exposé ici ; "
                "utilisez --content sfw ou --content nsfw."
            )
        if args.category or args.purity != "sfw" or args.rating != "safe":
            raise ValueError("Nekos.moe utilise --content et --tag.")


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.list_tags:
        if args.source != "waifu":
            parser.error("--list-tags fonctionne uniquement avec la source waifu.")
        try:
            list_waifu_tags()
            return 0
        except Exception as exc:
            print(f"Erreur : {exc}", file=sys.stderr)
            return 1

    try:
        validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    viewer = ImageViewer(
        source=args.source,
        initial_tags=args.tag,
        initial_content=args.content,
        initial_purity=args.purity,
        initial_rating=args.rating,
        initial_categories=args.category,
        auto_reload=args.auto_reload,
        timer_seconds=args.timer,
    )

    # Keep the instance alive for the GTK event loop.
    _ = viewer
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
