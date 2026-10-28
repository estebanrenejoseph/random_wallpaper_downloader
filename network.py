# network.py
import requests

HTTP_TIMEOUT = 30
USER_AGENT = "random-image-viewer(local desktop application)"

def http_get_json(url, params=None, headers=None):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    response = requests.get(url, params=params, headers=h, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"La réponse de {url} n'est pas un JSON valide (HTTP {response.status_code}).") from exc


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
