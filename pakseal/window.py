# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import os
import queue
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import policy  # noqa: E402
from .cpakhost import CpakError, CpakHost  # noqa: E402

SAVE_DELAY_MS = 500
ACCESS_LABELS = ("Read only", "Read and write")
ACCESS_VALUES = (policy.ACCESS_READ_ONLY, policy.ACCESS_READ_WRITE)
RISK_TOOLTIP = "Weakens the sandbox considerably"


class Worker:
    """Runs cpak calls one at a time off the main thread."""

    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, job, done):
        self._jobs.put((job, done))

    def _loop(self):
        while True:
            job, done = self._jobs.get()
            try:
                result, error = job(), None
            except CpakError as failure:
                result, error = None, failure
            GLib.idle_add(done, result, error)


class ListGroup:
    """A preferences group whose rows are rebuilt in place."""

    def __init__(self, group: Adw.PreferencesGroup, fill):
        self.group = group
        self.fill = fill
        self.rows: list[Gtk.Widget] = []

    def add(self, row):
        self.group.add(row)
        self.rows.append(row)

    def refresh(self):
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        self.fill(self)


class Window(Adw.ApplicationWindow):
    def __init__(self, application, host: CpakHost | None = None):
        super().__init__(application=application, title="Pakseal")
        self.set_default_size(980, 720)
        self.set_size_request(360, 360)

        self.host = host or CpakHost()
        self.worker = Worker()
        self.own_origin = os.environ.get("PAKSEAL_ORIGIN", "").lower()

        self.apps: dict[tuple[str, str], policy.Application] = {}
        # Policies edited in the interface and not yet confirmed by cpak.
        self.pending: dict[tuple[str, str], dict] = {}
        self.save_timers: dict[tuple[str, str], int] = {}
        self.in_flight: set[tuple[str, str]] = set()
        self.selected: tuple[str, str] | None = None
        self.toggle_rows: dict[str, tuple[policy.Toggle, Adw.SwitchRow]] = {}
        self.list_groups: dict[str, ListGroup] = {}
        self.updating = False
        self.closing = False

        self._build()
        self.connect("close-request", self._on_close_request)
        self.reload()

    # Layout

    def _build(self):
        self.split = Adw.NavigationSplitView(min_sidebar_width=260, max_sidebar_width=340)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)

        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.set_content(self.split)

    def _build_sidebar(self):
        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload Applications")
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_start(refresh)

        menu = Gio.Menu()
        menu.append("About Pakseal", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Main Menu", primary=True, menu_model=menu))

        self.search = Gtk.SearchEntry(placeholder_text="Search applications", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.app_list.invalidate_filter())
        search_bar = Gtk.Box(margin_start=12, margin_end=12, margin_bottom=6)
        search_bar.append(self.search)

        self.app_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.app_list.add_css_class("navigation-sidebar")
        self.app_list.set_filter_func(self._filter_app)
        self.app_list.connect("row-selected", self._on_app_selected)
        self.app_list.connect("row-activated", lambda *_: self.split.set_show_content(True))

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.app_list)

        spinner = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)
        spinner.set_size_request(32, 32)
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.set_valign(Gtk.Align.CENTER)

        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.add_named(scroller, "list")
        self.sidebar_stack.add_named(spinner, "loading")

        toolbar = Adw.ToolbarView(content=self.sidebar_stack)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(search_bar)
        return Adw.NavigationPage(title="Applications", child=toolbar)

    def _build_content(self):
        header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Pakseal")
        header.set_title_widget(self.window_title)

        self.reset_button = Gtk.Button(label="Reset", tooltip_text="Use the permissions the manifest asks for", visible=False)
        self.reset_button.add_css_class("destructive-action")
        self.reset_button.connect("clicked", self._on_reset_clicked)
        header.pack_end(self.reset_button)

        self.saving = Gtk.Spinner(tooltip_text="Saving", visible=False)
        header.pack_end(self.saving)

        self.banner = Adw.Banner()

        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.empty = Adw.StatusPage()
        self.content_stack.add_named(self.empty, "empty")
        self.toasts = Adw.ToastOverlay(child=self.content_stack)

        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.banner)
        self.content_page = Adw.NavigationPage(title="Permissions", child=toolbar)
        self._show_status()
        return self.content_page

    # Loading

    def reload(self):
        if self.has_unsaved_changes():
            self.toast("Wait until the changes are saved")
            return
        self.sidebar_stack.set_visible_child_name("loading")
        self.worker.submit(self.host.list, self._on_loaded)

    def _on_loaded(self, apps, error):
        self.sidebar_stack.set_visible_child_name("list")
        if error is not None:
            self.apps = {}
            self._fill_app_list()
            self._show_status("dialog-error-symbolic", "Could Not Read Permissions", str(error))
            return
        ordered = sorted(apps, key=lambda app: (app.name.lower(), app.origin, app.version))
        self.apps = {app.key: app for app in ordered}
        self._fill_app_list()
        if not self.apps:
            self._show_status(
                "system-software-install-symbolic",
                "No Applications",
                "Install an application with cpak to manage its permissions",
            )

    def _fill_app_list(self):
        previous = self.selected
        self.selected = None
        self.app_list.remove_all()
        versions: dict[str, int] = {}
        for origin, _ in self.apps:
            versions[origin] = versions.get(origin, 0) + 1
        selected_row = None
        for key, app in self.apps.items():
            subtitle = app.origin if versions[app.origin] == 1 else f"{app.version} · {app.origin}"
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(app.name),
                subtitle=GLib.markup_escape_text(subtitle),
                title_lines=1,
                subtitle_lines=1,
            )
            row.add_prefix(Gtk.Image(icon_name="application-x-executable-symbolic", pixel_size=24))
            marker = Gtk.Image(icon_name="document-edit-symbolic", tooltip_text="Changed by you")
            marker.add_css_class("dim-label")
            row.add_suffix(marker)
            row.pakseal_key = key
            row.pakseal_marker = marker
            self._update_app_row(row)
            self.app_list.append(row)
            if key == previous:
                selected_row = row
        if selected_row is not None:
            self.app_list.select_row(selected_row)
        elif self.apps:
            self._show_status()

    def _update_app_row(self, row):
        app = self.apps.get(row.pakseal_key)
        row.pakseal_marker.set_visible(bool(app and (app.customized or row.pakseal_key in self.pending)))

    def _app_row(self, key):
        row = self.app_list.get_first_child()
        while row is not None:
            if getattr(row, "pakseal_key", None) == key:
                return row
            row = row.get_next_sibling()
        return None

    def _filter_app(self, row):
        text = self.search.get_text().strip().lower()
        if not text:
            return True
        app = self.apps.get(row.pakseal_key)
        return app is not None and (text in app.name.lower() or text in app.origin.lower())

    def _show_status(self, icon=None, title=None, description=None):
        self.selected = None
        self.reset_button.set_visible(False)
        self.banner.set_revealed(False)
        self.content_page.set_title("Permissions")
        self.window_title.set_title("Pakseal")
        self.window_title.set_subtitle("")
        self.empty.set_icon_name(icon or "security-high-symbolic")
        self.empty.set_title(title or "No Application Selected")
        self.empty.set_description(description or "Select an application to see and change its permissions")
        self.content_stack.set_visible_child_name("empty")

    # Permission page

    def _on_app_selected(self, _list, row):
        if row is not None:
            self.selected = row.pakseal_key
            self._show_app()

    def current_policy(self, key) -> dict:
        if key in self.pending:
            return self.pending[key]
        return self.apps[key].policy

    def _customized(self, key) -> bool:
        return self.apps[key].customized or key in self.pending

    def _show_app(self):
        key = self.selected
        app = self.apps[key]
        current = self.current_policy(key)
        editable = app.origin != self.own_origin

        self.content_page.set_title(app.name)
        self.window_title.set_title(app.name)
        self.window_title.set_subtitle(f"{app.origin} · {app.version}")

        if not editable:
            self.banner.set_title("Pakseal cannot change its own permissions. Use cpak override on the host.")
            self.banner.set_revealed(True)
        elif app.override_error:
            self.banner.set_title("Your saved permissions could not be read, so the manifest applies. A change replaces them.")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        self.updating = True
        self.toggle_rows = {}
        self.list_groups = {}
        page = Adw.PreferencesPage()
        page.add(self._summary_group(app))
        for group in policy.GROUPS:
            if group.legacy and not policy.legacy_in_use(app.manifest, current):
                continue
            page.add(self._group(app, group, current))
        page.add(self._list_group("filesystem", page, "Files", "Folders shared with the application", self._fill_filesystem))
        page.add(self._host_actions_group(app, current))
        page.add(self._list_group("environment", page, "Environment", "Variables set inside the sandbox", self._fill_environment))
        rules = policy.session_bus_rules(current)
        if rules:
            page.add(self._session_bus_group(rules))
        page.set_sensitive(editable)
        self.updating = False
        self._refresh()

        old = self.content_stack.get_child_by_name("page")
        if old is not None:
            self.content_stack.remove(old)
        self.content_stack.add_named(page, "page")
        self.content_stack.set_visible_child(page)

    def _summary_group(self, app):
        group = Adw.PreferencesGroup()
        self.summary = Adw.ActionRow()
        self.summary.add_prefix(Gtk.Image(icon_name="security-high-symbolic"))
        group.add(self.summary)
        if app.pulled_in:
            dependency = Adw.ActionRow(
                title="Installed as a dependency",
                subtitle="The package that installed it can narrow what it may do",
            )
            dependency.add_prefix(Gtk.Image(icon_name="emblem-shared-symbolic"))
            group.add(dependency)
        return group

    def _group(self, app, group, current):
        widget = Adw.PreferencesGroup(title=group.title, description=group.description or None)
        for toggle in group.toggles:
            row = Adw.SwitchRow(title=toggle.title, subtitle=toggle.subtitle or None)
            row.set_active(bool(policy.get(current, toggle.key)))
            if toggle.risky:
                row.add_prefix(_warning_icon(RISK_TOOLTIP))
            row.connect("notify::active", self._on_toggle, toggle)
            self.toggle_rows[toggle.key] = (toggle, row)
            widget.add(row)
        for number in group.numbers:
            adjustment = Gtk.Adjustment(lower=0, upper=number.maximum, step_increment=1, page_increment=64)
            row = Adw.SpinRow(title=number.title, subtitle=_number_subtitle(number), adjustment=adjustment)
            row.set_value(int(policy.get(current, number.key) or 0))
            _mark_changed(row, policy.get(current, number.key) != policy.get(app.manifest, number.key))
            row.connect("notify::value", self._on_number, number)
            widget.add(row)
        return widget

    def _list_group(self, name, _page, title, description, fill):
        group = Adw.PreferencesGroup(title=title, description=description)
        self.list_groups[name] = ListGroup(group, fill)
        self.list_groups[name].refresh()
        return group

    def _refresh(self):
        """Bring the summary, markers and switch states in line with the policy."""
        key = self.selected
        if key is None:
            return
        app = self.apps[key]
        current = self.current_policy(key)
        editable = app.origin != self.own_origin

        self.reset_button.set_visible(editable and self._customized(key))
        if self._customized(key):
            self.summary.set_title("Your permissions")
            self.summary.set_subtitle("They replace what the manifest asks for. Changes apply the next time the application starts.")
        else:
            self.summary.set_title("Manifest permissions")
            self.summary.set_subtitle("What the application asks for. Changes apply the next time the application starts.")

        self.updating = True
        for toggle, row in self.toggle_rows.values():
            value = bool(policy.get(current, toggle.key))
            if row.get_active() != value:
                row.set_active(value)
            row.set_sensitive(value or policy.available(current, toggle))
            _mark_changed(row, value != bool(policy.get(app.manifest, toggle.key)))
        self.updating = False

    def _fill_filesystem(self, group):
        app = self.apps[self.selected]
        manifest = {(entry.get("path"), entry.get("access")) for entry in policy.filesystem(app.manifest)}
        for index, entry in enumerate(policy.filesystem(self.current_policy(self.selected))):
            label = policy.describe_location(entry["path"])
            row = Adw.ActionRow(title=GLib.markup_escape_text(label))
            if label != entry["path"]:
                row.set_subtitle(GLib.markup_escape_text(entry["path"]))
            if policy.filesystem_risky(entry):
                row.add_prefix(_warning_icon("Lets the application change files that run outside the sandbox"))
            access = Gtk.DropDown.new_from_strings(list(ACCESS_LABELS))
            access.set_valign(Gtk.Align.CENTER)
            access.set_selected(ACCESS_VALUES.index(entry["access"]) if entry["access"] in ACCESS_VALUES else 0)
            access.set_sensitive(entry["path"] != "host")
            access.connect("notify::selected", self._on_access_changed, index)
            row.add_suffix(access)
            row.add_suffix(_remove_button(self._on_filesystem_removed, index))
            _mark_changed(row, (entry["path"], entry["access"]) not in manifest)
            group.add(row)
        add = Adw.EntryRow(title="Add a location: home, ~/Games, xdg-download, host or /path", show_apply_button=True)
        add.connect("apply", self._on_filesystem_added)
        group.add(add)

    def _fill_environment(self, group):
        app = self.apps[self.selected]
        manifest = set(policy.environment(app.manifest))
        for index, entry in enumerate(policy.environment(self.current_policy(self.selected))):
            name, _, value = entry.partition("=")
            row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=GLib.markup_escape_text(value) or None)
            row.add_css_class("property")
            row.add_suffix(_remove_button(self._on_environment_removed, index))
            _mark_changed(row, entry not in manifest)
            group.add(row)
        add = Adw.EntryRow(title="Add a variable: NAME=value", show_apply_button=True)
        add.connect("apply", self._on_environment_added)
        group.add(add)

    def _host_actions_group(self, app, current):
        group = Adw.PreferencesGroup(title="Host Services", description="Typed services cpak runs on the host for the application")
        for action in policy.HOST_ACTIONS:
            row = Adw.SwitchRow(title=action.title, subtitle=action.subtitle or None)
            row.set_active(policy.host_action_enabled(current, action.provider, action.capability))
            if action.risky:
                row.add_prefix(_warning_icon(RISK_TOOLTIP))
            _mark_changed(row, row.get_active() != policy.host_action_enabled(app.manifest, action.provider, action.capability))
            row.connect("notify::active", self._on_host_action, action)
            group.add(row)
        return group

    def _session_bus_group(self, rules):
        group = Adw.PreferencesGroup(title="Session Bus")
        group.add(
            Adw.ActionRow(
                title=f"{rules} filtered session bus rule{'s' if rules != 1 else ''}",
                subtitle="Kept as they are. Edit them with cpak override edit on the host.",
            )
        )
        return group

    # Editing

    def _edit(self, update, refresh_group=None):
        key = self.selected
        if key is None or self.updating:
            return
        self.pending[key] = update(self.current_policy(key))
        row = self._app_row(key)
        if row is not None:
            self._update_app_row(row)
        if refresh_group is not None:
            self.list_groups[refresh_group].refresh()
        self._refresh()
        self._schedule_save(key)

    def _on_toggle(self, row, _param, toggle):
        self._edit(lambda current: policy.with_value(current, toggle.key, row.get_active()))

    def _on_number(self, row, _param, number):
        if self.updating:
            return
        value = int(row.get_value())
        _mark_changed(row, value != policy.get(self.apps[self.selected].manifest, number.key))
        self._edit(lambda current: policy.with_value(current, number.key, value))

    def _on_host_action(self, row, _param, action):
        if self.updating:
            return
        manifest = self.apps[self.selected].manifest
        _mark_changed(row, row.get_active() != policy.host_action_enabled(manifest, action.provider, action.capability))
        self._edit(lambda current: policy.with_host_action(current, action.provider, action.capability, row.get_active()))

    def _on_access_changed(self, dropdown, _param, index):
        def update(current):
            entries = policy.filesystem(current)
            entries[index]["access"] = ACCESS_VALUES[dropdown.get_selected()]
            return policy.with_filesystem(current, entries)

        # The dropdown is replaced while its signal is still being emitted.
        GLib.idle_add(lambda: self._edit(update, "filesystem") and False)

    def _on_filesystem_removed(self, _button, index):
        def update(current):
            entries = policy.filesystem(current)
            del entries[index]
            return policy.with_filesystem(current, entries)

        self._edit(update, "filesystem")

    def _on_filesystem_added(self, row):
        path = row.get_text().strip()
        if path == "~":
            path = "home"
        elif path.startswith("~/"):
            path = "home/" + path[2:].rstrip("/")
        entries = policy.filesystem(self.current_policy(self.selected))
        error = policy.filesystem_error(entries, path, policy.ACCESS_READ_ONLY)
        if error:
            self.toast(error)
            return
        entries.append({"path": path, "access": policy.ACCESS_READ_ONLY})
        self._edit(lambda current: policy.with_filesystem(current, entries), "filesystem")

    def _on_environment_removed(self, _button, index):
        def update(current):
            entries = policy.environment(current)
            del entries[index]
            return policy.with_environment(current, entries)

        self._edit(update, "environment")

    def _on_environment_added(self, row):
        entry = row.get_text().strip()
        entries = policy.environment(self.current_policy(self.selected))
        error = policy.environment_error(entries, entry)
        if error:
            self.toast(error)
            return
        entries.append(entry)
        self._edit(lambda current: policy.with_environment(current, entries), "environment")

    # Saving

    def _schedule_save(self, key):
        timer = self.save_timers.pop(key, None)
        if timer:
            GLib.source_remove(timer)
        self.save_timers[key] = GLib.timeout_add(SAVE_DELAY_MS, self._save, key)
        self._update_saving()

    def _save(self, key):
        self.save_timers.pop(key, None)
        # A change made while a request runs is sent when that request returns.
        if key in self.in_flight or key not in self.pending:
            return GLib.SOURCE_REMOVE
        sent = self.pending[key]
        app = self.apps[key]
        self.in_flight.add(key)
        self.worker.submit(
            lambda: self.host.set(app.origin, app.version, sent),
            lambda result, error: self._on_saved(key, sent, result, error),
        )
        self._update_saving()
        return GLib.SOURCE_REMOVE

    def _on_saved(self, key, sent, result, error):
        self.in_flight.discard(key)
        if error is not None:
            # Whatever was edited meanwhile built on the refused policy too.
            self.pending.pop(key, None)
            timer = self.save_timers.pop(key, None)
            if timer:
                GLib.source_remove(timer)
            self.toast(f"Could not save: {error}")
            self._reread(key)
        else:
            self.apps[key] = result
            if self.pending.get(key) is sent:
                del self.pending[key]
            elif key in self.pending and key not in self.save_timers:
                self._save(key)
            if key == self.selected:
                self._refresh()
        self._after_request(key)
        return GLib.SOURCE_REMOVE

    def _reread(self, key):
        """Show what cpak holds for one application after a failed request.

        cpak can refuse a policy before writing it, or write it and then fail
        to re-enrol the application, so the stored state is read back rather
        than assumed.
        """
        self.in_flight.add(key)

        def done(apps, error):
            self.in_flight.discard(key)
            for app in apps or []:
                if app.key == key:
                    self.apps[key] = app
            if key == self.selected and key not in self.pending:
                self._show_app()
            self._after_request(key)

        self.worker.submit(self.host.list, done)

    def _after_request(self, key):
        row = self._app_row(key)
        if row is not None:
            self._update_app_row(row)
        self._update_saving()
        if self.closing and not self.has_unsaved_changes():
            self.destroy()

    def _update_saving(self):
        busy = self.has_unsaved_changes()
        self.saving.set_visible(busy)
        self.saving.set_spinning(busy)

    def has_unsaved_changes(self) -> bool:
        return bool(self.pending or self.in_flight)

    def _on_close_request(self, _window):
        if not self.has_unsaved_changes():
            return False
        # Finish saving before the application exits.
        self.closing = True
        for key in list(self.save_timers):
            GLib.source_remove(self.save_timers.pop(key))
            self._save(key)
        self.set_visible(False)
        return True

    # Reset

    def _on_reset_clicked(self, _button):
        app = self.apps[self.selected]
        dialog = Adw.AlertDialog(
            heading="Reset Permissions?",
            body=f"{app.name} will use the permissions its manifest asks for. Your changes will be lost.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reset_response, app.key)
        dialog.present(self)

    def _on_reset_response(self, _dialog, response, key):
        if response != "reset":
            return
        timer = self.save_timers.pop(key, None)
        if timer:
            GLib.source_remove(timer)
        self.pending.pop(key, None)
        app = self.apps[key]
        self.in_flight.add(key)
        self._update_saving()

        def done(result, error):
            self.in_flight.discard(key)
            if error is not None:
                self.toast(f"Could not reset: {error}")
                self._reread(key)
            else:
                self.apps[key] = result
                self.toast(f"{app.name} uses its manifest permissions again")
                if key == self.selected:
                    self._show_app()
            self._after_request(key)

        self.worker.submit(lambda: self.host.reset(app.origin, app.version), done)

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=5))


def _mark_changed(row, changed):
    if changed:
        row.add_css_class("pakseal-changed")
    else:
        row.remove_css_class("pakseal-changed")


def _warning_icon(tooltip):
    icon = Gtk.Image(icon_name="dialog-warning-symbolic", tooltip_text=tooltip)
    icon.add_css_class("warning")
    return icon


def _remove_button(callback, index):
    button = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove", valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.connect("clicked", callback, index)
    return button


def _number_subtitle(number):
    return f"{number.subtitle} ({number.unit})" if number.unit else number.subtitle
