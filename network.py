# network.py

"""HTTP network utility module for managing external API requests and asset downloads."""

from typing import Any, Dict, Optional, Tuple
import requests

HTTP_TIMEOUT: int = 30
USER_AGENT: str = "random-wallpaper-viewer(local desktop application)"


def http_get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    """Executes an HTTP GET request and returns the decoded JSON response.

    Args:
        url: The target URL endpoint.
        params: Optional dictionary of query string parameters.
        headers: Optional HTTP headers to merge with default request headers.

    Returns:
        Any: Parsed JSON content (typically a dictionary or list).

    Raises:
        requests.exceptions.HTTPError: If the server returns an HTTP error status.
        RuntimeError: If the response body cannot be parsed as valid JSON.
    """
    request_headers: Dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        request_headers.update(headers)

    response = requests.get(
        url,
        params=params,
        headers=request_headers,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"The response from {url} is not valid JSON "
            f"(HTTP {response.status_code})."
        ) from exc


def http_post_json(
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    """Executes an HTTP POST request with a JSON payload and returns the decoded JSON response.

    Args:
        url: The target URL endpoint.
        payload: Optional dictionary to send as JSON in the request body.
        headers: Optional HTTP headers to merge with default request headers.

    Returns:
        Any: Parsed JSON content (typically a dictionary or list).

    Raises:
        requests.exceptions.HTTPError: If the server returns an HTTP error status.
        RuntimeError: If the response body cannot be parsed as valid JSON.
    """
    request_headers: Dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        request_headers.update(headers)

    response = requests.post(
        url,
        json=payload or {},
        headers=request_headers,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"The response from {url} is not valid JSON "
            f"(HTTP {response.status_code})."
        ) from exc


def http_get_bytes(
    url: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[bytes, str]:
    """Downloads binary asset content directly into memory without writing to disk.

    Args:
        url: Direct HTTP/HTTPS URL of the image file.
        headers: Optional HTTP headers to merge with default request headers.

    Returns:
        Tuple[bytes, str]: Tuple containing raw binary payload and normalized MIME type.

    Raises:
        requests.exceptions.HTTPError: If the server returns an HTTP error status.
        RuntimeError: If the downloaded binary payload is empty.
    """
    request_headers: Dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    if headers:
        request_headers.update(headers)

    response = requests.get(
        url,
        headers=request_headers,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    content = response.content
    if not content:
        raise RuntimeError("Downloaded image payload is empty.")

    content_type = (
        response.headers.get("Content-Type", "application/octet-stream")
        .split(";", 1)[0]
        .strip()
        .lower()
    )
    return content, content_type
