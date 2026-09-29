# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import sys
from importlib import resources

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import APPLICATION_ID, VERSION  # noqa: E402
from .window import Window  # noqa: E402


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._add_action("about", self._on_about)
        self._add_action("quit", lambda *_: self._quit())
        self.set_accels_for_action("app.quit", ["<primary>q"])
        self.set_accels_for_action("window.close", ["<primary>w"])

    def _add_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_string(resources.files(__package__).joinpath("style.css").read_text())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        window = self.get_active_window() or Window(self)
        window.present()

    def _quit(self):
        # Closing the window finishes any save in progress first.
        for window in self.get_windows():
            window.close()

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name="Pakseal",
            application_icon=APPLICATION_ID,
            version=VERSION,
            comments="Review and change the permissions of cpak applications",
            license_type=Gtk.License.AGPL_3_0,
            website="https://codeberg.org/nosini/pakseal",
            developer_name="Nosini",
        )
        about.present(self.get_active_window())


def main() -> int:
    return Application().run(sys.argv)
