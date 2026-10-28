#!/usr/bin/env python3

"""Application entry point for Random Wallpaper Viewer.

Initializes the GTK3 runtime environment, configures default application parameters,
instantiates the primary user interface, and executes the GTK main event loop.
"""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from gui import ImageViewer


def main() -> int:
    """Instantiates the GUI viewer and starts the GTK main event loop.

    Returns:
        int: Process exit status code (0 for clean execution).
    """
    viewer = ImageViewer(
        source="nasa", # Source by default at launch is arbitrary nasa
        initial_tags=None,
        initial_content="sfw",
        initial_purity="sfw",
        initial_rating="safe",
        initial_categories=None,
        auto_reload=False,
        timer_seconds=30,
    )

    # Retain instance reference in scope to prevent garbage collection
    # during GTK event loop execution and satisfy static analysis tools (F841).
    _ = viewer

    # Hand over control to the GTK main event loop (blocking call)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
