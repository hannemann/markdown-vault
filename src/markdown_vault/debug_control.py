"""Dev-only D-Bus interface to drive the running app for debugging/automation.

Gated by the ``MDV_DEBUG_CONTROL`` environment variable — when unset, nothing is
registered and the interface does not exist. When set, it exports
``de.hannemann.markdown_vault.Debug`` at ``/de/hannemann/markdown_vault/debug`` on
the app's session-bus connection, so a developer (or a test / an MCP bridge) can
open and close notes, run searches and read back state as if clicking in the UI.

D-Bus delivers method calls on the connection's thread-default context — the GTK
main loop — so the handlers run on the UI thread and may touch widgets directly.
The commands are non-destructive: open/close/search/select and read-back only, no
create/rename/delete.

Exposure (accepted, dev-only): the interface is registered on the session bus, so
while it is enabled any session peer can call it — and Search+SearchResults is a
content oracle over the vault. This is tolerated because the flag is set only by
the development launcher (scripts/app.sh), never by the shipped .desktop entry, so
it does not exist during normal use. If it ever needs hardening, move it onto a
private peer-to-peer socket (0600) in $XDG_RUNTIME_DIR instead of the session bus.
"""

import logging

from gi.repository import Gio, GLib

logger = logging.getLogger(__name__)

ENV_FLAG = "MDV_DEBUG_CONTROL"
INTERFACE = "de.hannemann.markdown_vault.Debug"

_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="OpenFile">
      <arg type="s" name="path" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="CloseTab">
      <arg type="s" name="path" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="Search">
      <arg type="s" name="query" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="QuickOpen">
      <arg type="s" name="query" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="Submit">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="AskAnswer">
      <arg type="s" name="markdown" direction="out"/>
    </method>
    <method name="SelectInTree">
      <arg type="s" name="path" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ActiveFile">
      <arg type="s" name="path" direction="out"/>
    </method>
    <method name="ListTabs">
      <arg type="as" name="paths" direction="out"/>
    </method>
    <method name="DumpState">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="SearchResults">
      <arg type="as" name="paths" direction="out"/>
    </method>
    <method name="WaitIdle">
      <arg type="i" name="timeout_ms" direction="in"/>
      <arg type="b" name="settled" direction="out"/>
    </method>
  </interface>
</node>
"""


class DebugControl:
    """Registers the debug interface and dispatches its calls to the window."""

    def __init__(self, app, connection, base_object_path: str) -> None:
        self._app = app
        self._conn = connection
        self._path = base_object_path.rstrip("/") + "/debug"
        self._reg_id = 0

    def register(self) -> None:
        info = Gio.DBusNodeInfo.new_for_xml(_XML).interfaces[0]
        self._reg_id = self._conn.register_object(
            self._path, info, self._on_method_call, None, None)
        logger.warning("debug control interface active at %s (%s set)",
                       self._path, ENV_FLAG)

    def unregister(self) -> None:
        if self._reg_id:
            self._conn.unregister_object(self._reg_id)
            self._reg_id = 0

    # ------------------------------------------------------------------

    def _on_method_call(self, _connection, _sender, _object_path, _interface,
                        method, parameters, invocation) -> None:
        win = self._app.get_active_window()
        if win is None:
            invocation.return_dbus_error(INTERFACE + ".Error", "no window")
            return
        try:
            result = self._dispatch(win, method, parameters)
        except Exception as exc:  # never let an exception cross the bus boundary
            logger.warning("debug control %s failed", method, exc_info=True)
            invocation.return_dbus_error(INTERFACE + ".Error", str(exc))
            return
        invocation.return_value(result)

    @staticmethod
    def _dispatch(win, method: str, params) -> GLib.Variant:
        if method == "OpenFile":
            return GLib.Variant("(b)", (win.debug_open_file(params.unpack()[0]),))
        if method == "CloseTab":
            return GLib.Variant("(b)", (win.debug_close_tab(params.unpack()[0]),))
        if method == "Search":
            return GLib.Variant("(b)", (win.debug_search(params.unpack()[0]),))
        if method == "QuickOpen":
            return GLib.Variant("(b)", (win.debug_quick_open(params.unpack()[0]),))
        if method == "Submit":
            return GLib.Variant("(b)", (win.debug_submit(),))
        if method == "AskAnswer":
            return GLib.Variant("(s)", (win.debug_ask_answer(),))
        if method == "SelectInTree":
            return GLib.Variant("(b)", (win.debug_select_in_tree(params.unpack()[0]),))
        if method == "ActiveFile":
            return GLib.Variant("(s)", (win.debug_active_file(),))
        if method == "ListTabs":
            return GLib.Variant("(as)", (win.debug_list_tabs(),))
        if method == "DumpState":
            return GLib.Variant("(s)", (win.debug_state(),))
        if method == "SearchResults":
            return GLib.Variant("(as)", (win.debug_search_results(),))
        if method == "WaitIdle":
            return GLib.Variant("(b)", (win.debug_wait_idle(params.unpack()[0]),))
        raise ValueError(f"unknown method {method}")


def maybe_register(app, connection, base_object_path: str):
    """Register the debug interface when ``MDV_DEBUG_CONTROL`` is set; else a no-op
    returning ``None``. Returns the :class:`DebugControl` so the app can keep it
    alive and unregister on shutdown."""
    import os
    if not os.environ.get(ENV_FLAG):
        return None
    control = DebugControl(app, connection, base_object_path)
    control.register()
    return control
