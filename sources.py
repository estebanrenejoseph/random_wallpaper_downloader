# sources.py
import os
import random
from models import SourceSpec
from network import http_get_json, http_post_json

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

