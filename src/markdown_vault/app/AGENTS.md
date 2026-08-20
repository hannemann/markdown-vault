# app

The window shell plus the objects that took behaviour off it. `MainWindow` used
to own everything the window could do; what is left is genuine window work
(vaults, tabs, actions that need window state).

## Two kinds of neighbours

- **`*_manager`** — coordinate a flow the window kicks off: files, input,
  monitor events, session, tabs, view mode. The window drives them.
- **`*_controller`** and the named objects (`LinkNavigator`, `PreviewActions`) —
  own a domain **together with its state** and register their **own** actions.
  The window builds them and then stays out of the way. `ScrollMemory` is the
  lighter case: a named collaborator with no actions and no state of its own — it
  earns its module because the responsibility it carries (reading position ↔
  history entry) belongs to neither of its collaborators, not because of rule 1
  or 3. It is handed to the InputManager as **one object** (rule 3): the manager
  calls its `save_leaving()` / `restore_current(in_page)` itself, rather than
  being assembled from a pair of `*_fn` callbacks at the call site.

## Rules for the next cut

1. **An own object only if there is state** that moves with it (zoom: pointer
   position; zen: the pre-zen baseline; find: target and handler). Without state
   the coupling stays, it just changes address.
2. **Move ownership** when a collaborator already has the behaviour and the
   window only owned the trigger.
3. **Hand over a surface as one object** instead of assembling it from callbacks
   at the call site.
4. A domain registers its actions **itself**
   (`register_actions(window: Gio.ActionMap)`). Moving only the registration and
   leaving the handlers in the window lowers the metric and changes nothing.
5. Take **no `MainWindow` reference** — narrow getters and collaborators. A
   `Controller(self)` only inverts the coupling.

**Ordering:** every object that registers its own actions must be built before
`_register_actions()`. A violation fails during construction, so
`tests/test_app_window_construction.py` catches it.

**Metric:** `make callbacks FILE=src/markdown_vault/app/app_window.py` — how many
window methods are handed outward. Currently 85 methods / 107 sites.

Every new `.py` here also goes into `meson.build` (alphabetically), or it is not
installed and the app dies with `ModuleNotFoundError`.
