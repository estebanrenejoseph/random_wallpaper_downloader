#!/usr/bin/env python3
# main.py

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from gui import ImageViewer

def main():
    viewer = ImageViewer(
        source="nasa",
        initial_tags=None,
        initial_content="sfw",
        initial_purity="sfw",
        initial_rating="safe",
        initial_categories=None,
        auto_reload=False,
        timer_seconds=30
    )
    
    # La variable sert à garder la référence active
    _ = viewer 
    
    # Lancement de la boucle GTK
    Gtk.main()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
