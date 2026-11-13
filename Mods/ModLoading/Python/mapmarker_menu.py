# -*- coding: utf-8 -*-
"""mapmarker_menu - Content Browser right-click action for DA_MapMarker_V2 assets.

Editor-only (imports `unreal`). Call register() once at editor startup, e.g.
from the SDK's Content/Python/init_unreal.py (guarded; safe to call twice):

    try:
        import mapmarker_menu; mapmarker_menu.register()
    except Exception as error:
        unreal.log_warning('mapmarker menu: {0}'.format(error))

The entry only appears when exactly one asset is selected and it has the map
marker's path property. Running it adds the marker folder's standalone base
maps (see mapmarker.populate_shipped) without loading any map. Two more entries
maintain IgnoredMaps and the derived ShippedMapLayers.
"""
import unreal

import mapmarker

MENU_NAME = 'ContentBrowser.AssetContextMenu'
SECTION = 'ArgonMapMarker'
OWNER = 'ArgonSDKTooling.MapMarker'


def _log(message):
    unreal.log('[mapmarker] ' + message)


def _set_icon(entry, icon, toolbar=False):
    """Best effort: a missing icon name only leaves the button without one.

    All icons here exist as a 40px brush plus '<name>.Small' (20px). Toolbar buttons get both
    (large normally, small when the toolbar is set to small icons); menu rows use the small one.
    """
    try:
        if toolbar:
            entry.set_icon('EditorStyle', icon, icon + '.Small')
        else:
            entry.set_icon('EditorStyle', icon + '.Small')
    except Exception as error:  # noqa: BLE001
        _log('icon {0} not set: {1}'.format(icon, error))


def _is_map_marker(asset):
    try:
        asset.get_editor_property(mapmarker.MAPS_PROPERTY)
        return True
    except Exception:
        return False


def _message_box(title, text, yes_no=False):
    """Modal Windows message box. 4.25 Python has no unreal.EditorDialog.

    Returns True for OK/Yes, False for No, and None when no dialog could be shown
    (the text is in the Output Log either way). The argument types MUST be declared:
    without them ctypes fails the call (returns 0, 'invalid window handle').
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        user32.MessageBoxW.restype = ctypes.c_int
        flags = 0x00010000 | 0x00040000          # MB_SETFOREGROUND | MB_TOPMOST
        flags |= (0x4 | 0x20) if yes_no else (0x0 | 0x40)   # YESNO+QUESTION / OK+INFORMATION
        answer = user32.MessageBoxW(user32.GetForegroundWindow(), u'%s' % text, u'%s' % title, flags)
        if answer in (1, 6):                     # IDOK, IDYES
            return True
        if answer in (2, 7):                     # IDCANCEL, IDNO
            return False
        _log('message box failed (returned {0})'.format(answer))
        return None
    except Exception as error:  # noqa: BLE001
        _log('no message box available: {0}'.format(error))
        return None


_UI_STATE = {}


def _ui_file():
    import os
    return os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()),
                        'mapmarker_ui.json')


def _popups_on():
    """The toolbar 'Popups' toggle (remembered between editor sessions; off by default)."""
    if 'popups' not in _UI_STATE:
        try:
            import json
            with open(_ui_file()) as handle:
                _UI_STATE['popups'] = bool(json.load(handle).get('popups', False))
        except Exception:
            _UI_STATE['popups'] = False
    return _UI_STATE['popups']


def _set_popups(value):
    _UI_STATE['popups'] = bool(value)
    try:
        import json
        with open(_ui_file(), 'w') as handle:
            json.dump({'popups': _UI_STATE['popups']}, handle)
    except Exception as error:  # noqa: BLE001
        _log('could not remember the popups setting: {0}'.format(error))


def _say(title, lines, always=False):
    """Report a result: always to the log. A message box when `always` (Validate, List required
    maps, Clear) or when the toolbar 'Popups' toggle is on; otherwise a short on-screen message
    (first line only)."""
    for line in lines:
        _log(line)
    if always or _popups_on():
        _message_box(title, u'\n'.join(u'%s' % line for line in lines))
        return
    try:
        unreal.SystemLibrary.print_string(None, u'[Map marker] %s' % lines[0], True, False,
                                          unreal.LinearColor(0.3, 0.9, 0.4, 1.0), 6.0)
    except Exception:
        pass


def _run(title, action):
    try:
        return action()
    except Exception as error:  # noqa: BLE001
        unreal.log_error('[mapmarker] {0}: {1}'.format(title, error))
        _message_box(title, u'Failed:\n%s' % error)
        return None


def run_populate(package_path):
    """Menu: add base maps that are neither shipped nor ignored."""
    r = _run('Populate shipped maps', lambda: mapmarker.populate_shipped(package_path))
    if r is None:
        return
    lines = ['Added {0} base map(s); kept {1}; {2} ignored; {3} layer(s) skipped; {4} excluded.'.format(
        len(r['added']), r['kept'], r['ignored'], len(r['layers']), len(r['excluded']))]
    if r['stale']:
        lines.append('{0} shipped entr(ies) point at maps that no longer exist (left untouched).'.format(len(r['stale'])))
    if r['conflicts']:
        lines.append('{0} map(s) are both shipped and ignored.'.format(len(r['conflicts'])))
    if r['cyclic']:
        lines.append('{0} map(s) only reference each other in a cycle; treated as base.'.format(len(r['cyclic'])))
    _say('Populate shipped maps', lines + ['added ' + p for p in r['added']])


def run_ignore_unused(package_path):
    """Menu: put base maps that are not shipped into IgnoredMaps."""
    r = _run('Ignore unused maps', lambda: mapmarker.ignore_unused(package_path))
    if r is None:
        return
    _say('Ignore unused maps', ['Ignored {0} more map(s) ({1} ignored in total, {2} shipped).'.format(
        len(r['ignored_added']), r['ignored_total'], r['shipped'])] + ['ignored ' + p for p in r['ignored_added']])


def run_update_layers(package_path):
    """Menu: recompute ShippedMapLayers from the shipped maps."""
    r = _run('Update map layers', lambda: mapmarker.update_layers(package_path))
    if r is None:
        return
    _say('Update map layers', ['{0} shipped map(s) need {1} layer(s): {2} added, {3} removed.'.format(
        r['shipped'], r['layers'], len(r['added']), len(r['removed']))]
        + ['+ ' + p for p in r['added']] + ['- ' + p for p in r['removed']])


def _short(paths, limit=12):
    names = [p.split('/')[-1].split('.')[0] for p in paths]
    more = ['... and {0} more (see the Output Log)'.format(len(names) - limit)] if len(names) > limit else []
    return names[:limit] + more


def run_preview(package_path):
    """Menu: show what Populate would add, without changing anything."""
    r = _run('Preview populate', lambda: mapmarker.populate_shipped(package_path, dry_run=True))
    if r is None:
        return
    for p in r['added']:
        _log('would add ' + p)
    head = 'Populate would add {0} base map(s) ({1} already shipped, {2} ignored, {3} layer(s) skipped, {4} excluded).'.format(
        len(r['added']), r['kept'], r['ignored'], len(r['layers']), len(r['excluded']))
    _say('Preview populate', [head] + _short(r['added']))


def run_validate(package_path):
    """Menu: read-only audit of the marker."""
    r = _run('Validate map marker', lambda: mapmarker.validate(package_path))
    if r is None:
        return
    c = r['counts']
    summary = '{0} shipped, {1} ignored, {2} extra, {3} layers; {4} base map(s) in the folder, {5} excluded.'.format(
        c['shipped'], c['ignored'], c['extra'], c['layers'], c['base_in_folder'], c['excluded'])
    for line in r['issues']:
        unreal.log_warning('[mapmarker] ' + line)
    if r['ok']:
        _say('Validate map marker', ['No problems found.', summary], always=True)
    else:
        _say('Validate map marker', ['{0} problem(s):'.format(len(r['issues']))] + _short(r['issues'], 15) + [summary], always=True)


def run_refresh_summaries(package_path):
    """Menu: re-pull every row's summary (name/description/gamemode/faction) from the Asset Registry.

    This is the write counterpart to Validate: Validate only reports drifted or missing
    summaries, Populate only fills summaries on maps it newly adds. Use this after adding a
    map to the marker by hand, or when Validate reports a summary is out of date.
    """
    r = _run('Refresh map summaries', lambda: mapmarker.refresh_summaries(package_path))
    if r is None:
        return
    lines = ['Refreshed {0} map summar(ies); {1} map(s) have no Asset Registry summary.'.format(
        len(r['updated']), len(r['missing']))]
    if r['missing']:
        lines.append('No summary - open and re-save the map(s): ' + ', '.join(_short(r['missing'], 8)))
    for p in r['updated']:
        _log('refreshed ' + p)
    _say('Refresh map summaries', lines + ['refreshed ' + p for p in r['updated']])


def run_list_maps(package_path):
    """Menu: list what a cook of this marker needs - shipped, extra and layer maps."""
    r = _run('List required maps', lambda: mapmarker.required_maps(package_path))
    if r is None:
        return
    name = lambda p: p.split('/')[-1]  # noqa: E731
    for label in ('shipped', 'extra', 'layers'):
        for p in r[label]:
            _log('{0}: {1}'.format(label, p))
    lines = ['{0} shipped, {1} extra, {2} layer(s) = {3} map(s) to cook.'.format(
        len(r['shipped']), len(r['extra']), len(r['layers']), len(r['shipped']) + len(r['extra']) + len(r['layers']))]
    for label, title in (('shipped', 'Shipped'), ('extra', 'Extra'), ('layers', 'Layers')):
        if r[label]:
            lines.append('{0}: {1}'.format(title, ', '.join(_short([name(p) for p in r[label]], 8))))
    lines.append('Full paths are in the Output Log.')
    _say('List required maps', lines, always=True)


# Old name (first menu version).
run_reveal = run_list_maps


def run_clear(package_path):
    """Menu: empty shipped / ignored / layers after a confirmation (keeps the settings)."""
    c = _run('Clear maps', lambda: mapmarker.clear_maps(package_path, dry_run=True))
    if c is None:
        return
    if not (c['shipped'] or c['ignored'] or c['layers']):
        _say('Clear maps', ['Nothing to clear.'])
        return
    message = ('Clear {0} shipped map(s), {1} ignored map(s) and {2} layer(s)?\n'
               'Extra maps, exclude fields and notes are kept.').format(c['shipped'], c['ignored'], c['layers'])
    if c['with_loading_screen']:
        message += '\n\n{0} shipped map(s) have a custom loading screen that will be lost.'.format(c['with_loading_screen'])
    answer = _message_box('Clear maps', message, yes_no=True)
    if answer is not True:
        _log('clear cancelled' if answer is False else 'clear needs a dialog to confirm and none could be shown')
        return
    r = _run('Clear maps', lambda: mapmarker.clear_maps(package_path))
    if r is not None:
        _say('Clear maps', ['Cleared {0} shipped, {1} ignored, {2} layer(s).'.format(r['shipped'], r['ignored'], r['layers'])])


# Kept for the first version of the menu / any saved commands.
run_init = run_populate


ACTIONS = (
    # (menu entry name, label, function, tooltip, EditorStyle icon, toolbar label)
    ('ArgonMapMarkerPopulate', 'Populate shipped maps', 'run_populate',
     'Add this folder\'s standalone base maps (not sublevel layers) to ShippedMapPaths, skipping ignored, '
     'extra and excluded ones. Keeps existing entries; loads no maps.',
     'LevelEditor.Create', 'Populate'),
    ('ArgonMapMarkerPreview', 'Preview populate', 'run_preview',
     'Show which maps Populate would add, without changing the marker.',
     'LevelEditor.ViewOptions', 'Preview'),
    ('ArgonMapMarkerIgnore', 'Ignore unused maps', 'run_ignore_unused',
     'Add base maps that are not in ShippedMapPaths to IgnoredMaps so Populate leaves them out.',
     'GraphEditor.ToggleHideUnrelatedNodes', 'Ignore unused'),
    ('ArgonMapMarkerLayers', 'Update map layers', 'run_update_layers',
     'Recompute ShippedMapLayers: the sublevels the shipped and extra maps stream in (what a cook needs).',
     'LevelEditor.LandscapeMode', 'Update layers'),
    ('ArgonMapMarkerValidate', 'Validate marker', 'run_validate',
     'Read-only audit: stale or conflicting entries, shipped maps that are layers, out-of-date layers.',
     'LevelEditor.MapCheck', 'Validate'),
    ('ArgonMapMarkerRefresh', 'Refresh map summaries', 'run_refresh_summaries',
     'Re-pull each shipped map\'s name, description, gamemode and faction from the Asset Registry '
     '(fixes summaries Validate flags as missing or out of date, e.g. after adding a map by hand). '
     'Keeps State and loading screens; loads no maps.',
     'AssetEditor.ReimportAsset', 'Refresh'),
    ('ArgonMapMarkerList', 'List required maps', 'run_list_maps',
     'List the maps a cook of this marker needs: shipped, extra and their layers.',
     'LevelEditor.WorldProperties', 'List maps'),
    ('ArgonMapMarkerClear', 'Clear maps', 'run_clear',
     'Empty ShippedMapPaths, IgnoredMaps and ShippedMapLayers (asks first; keeps extra maps, excludes and notes).',
     'SessionConsole.Clear', 'Clear'),
)


@unreal.uclass()
class MapMarkerMenuSection(unreal.ToolMenuSectionDynamic):
    @unreal.ufunction(override=True)
    def construct_sections(self, menu, context):
        try:
            asset_context = context.find_by_class(unreal.ContentBrowserAssetContextMenuContext)
            if not asset_context:
                return
            selected = asset_context.get_selected_objects()
            if len(selected) != 1 or not _is_map_marker(selected[0]):
                return
            package = selected[0].get_outermost().get_name()
            menu.add_section(section_name=SECTION, label='Map marker')
            for name, label, function, tip, icon, _short_label in ACTIONS:
                entry = unreal.ToolMenuEntry(name=name, type=unreal.MultiBlockType.MENU_ENTRY)
                entry.set_label(label)
                entry.set_tool_tip(tip)
                _set_icon(entry, icon)
                entry.set_string_command(
                    type=unreal.ToolMenuStringCommandType.PYTHON, custom_type=unreal.Name(''),
                    string='import mapmarker_menu; mapmarker_menu.{0}(r"{1}")'.format(function, package))
                menu.add_menu_entry(SECTION, entry)
        except Exception as error:  # noqa: BLE001
            unreal.log_error('[mapmarker] menu construction failed: {0}'.format(error))


TOOLBAR_NAME = 'AssetEditor.DefaultToolBar'
TOOLBAR_SECTION = 'ArgonMapMarkerToolbar'


@unreal.uclass()
class PopupToggleEntry(unreal.ToolMenuEntryScript):
    """Toolbar check button: show a message box after Populate / Preview / Ignore unused / Update layers."""

    @unreal.ufunction(override=True)
    def execute(self, context):
        _set_popups(not _popups_on())
        unreal.ToolMenus.get().refresh_all_widgets()

    @unreal.ufunction(override=True)
    def get_check_state(self, context):
        return unreal.CheckBoxState.CHECKED if _popups_on() else unreal.CheckBoxState.UNCHECKED


def _make_toggle():
    entry = PopupToggleEntry()
    entry.init_entry(OWNER, TOOLBAR_NAME, TOOLBAR_SECTION, 'ArgonMapMarkerPopupsTb', 'Popups',
                     'When ticked, Populate / Preview / Ignore unused / Update layers / Refresh map summaries show a message box with the '
                     'result. Off: the result goes to the Output Log with a short on-screen note. Validate, List '
                     'maps and Clear always show one.')
    data = entry.get_editor_property('data')
    advanced = data.get_editor_property('advanced')
    # entry_type defaults to a menu row, which toolbars reject ("type not currently supported")
    advanced.set_editor_property('entry_type', unreal.MultiBlockType.TOOL_BAR_BUTTON)
    advanced.set_editor_property('user_interface_action_type', unreal.UserInterfaceActionType.TOGGLE_BUTTON)
    data.set_editor_property('advanced', advanced)
    try:
        icon = data.get_editor_property('icon')
        icon.set_editor_property('style_set_name', 'EditorStyle')
        icon.set_editor_property('style_name', 'LevelEditor.GameSettings')
        icon.set_editor_property('small_style_name', 'LevelEditor.GameSettings.Small')
        data.set_editor_property('icon', icon)
    except Exception as error:  # noqa: BLE001
        _log('popups toggle icon not set: {0}'.format(error))
    entry.set_editor_property('data', data)
    return entry



@unreal.uclass()
class MapMarkerToolbarSection(unreal.ToolMenuSectionDynamic):
    """Toolbar buttons for the map marker's asset editor - shown only for markers.

    Every asset editor's toolbar inherits AssetEditor.DefaultToolBar, and 4.25 Python cannot
    see which asset a toolbar belongs to. A toolbar is built when its editor OPENS, and the
    asset you just opened is the Content Browser selection, so: add the buttons only when
    exactly that selection is one map marker, and bake its path into each button. Opening a
    marker by a route that leaves another asset selected gets no (or the selected marker's)
    buttons; the right-click menu always works.
    """

    @unreal.ufunction(override=True)
    def construct_sections(self, menu, context):
        try:
            selected = unreal.EditorUtilityLibrary.get_selected_assets()
            if len(selected) != 1 or not _is_map_marker(selected[0]):
                return
            package = selected[0].get_outermost().get_name()
            marker = selected[0].get_name()
            menu.add_section(section_name=TOOLBAR_SECTION, label='')
            for name, label, function, tip, icon, short_label in ACTIONS:
                entry = unreal.ToolMenuEntry(name=name + 'Tb', type=unreal.MultiBlockType.TOOL_BAR_BUTTON)
                entry.set_label(short_label)
                _set_icon(entry, icon, toolbar=True)
                entry.set_tool_tip('{0} ({1})'.format(tip, marker))
                entry.set_string_command(
                    type=unreal.ToolMenuStringCommandType.PYTHON, custom_type=unreal.Name(''),
                    string='import mapmarker_menu; mapmarker_menu.{0}(r"{1}")'.format(function, package))
                menu.add_menu_entry(TOOLBAR_SECTION, entry)
            menu.add_menu_entry_object(_POPUP_TOGGLE)
            _log('toolbar buttons added for ' + marker)
        except Exception as error:  # noqa: BLE001
            unreal.log_error('[mapmarker] toolbar construction failed: {0}'.format(error))


# Keep the section objects alive for the Python session (GC would drop the menus).
try:
    _SECTION
except NameError:
    _SECTION = MapMarkerMenuSection()
try:
    _TOOLBAR_SECTION
except NameError:
    _TOOLBAR_SECTION = MapMarkerToolbarSection()
try:
    _POPUP_TOGGLE
except NameError:
    _POPUP_TOGGLE = _make_toggle()


def register(toolbar=True):
    """Attach the dynamic sections (context menu, and toolbar of marker editors). Idempotent."""
    tool_menus = unreal.ToolMenus.get()
    menu = tool_menus.extend_menu(MENU_NAME)
    if not menu:
        raise RuntimeError('could not extend ' + MENU_NAME)
    menu.add_dynamic_section(SECTION, _SECTION)
    if toolbar:
        tool_menus.extend_menu(TOOLBAR_NAME).add_dynamic_section(TOOLBAR_SECTION, _TOOLBAR_SECTION)
    tool_menus.refresh_all_widgets()
    _log('menus registered')
    try:  # the V2 mod marker menus ride on the same startup line
        import modmarker_menu
        modmarker_menu.register(toolbar)
    except ImportError:
        pass
    except Exception as error:  # noqa: BLE001
        unreal.log_warning('[mapmarker] mod marker menus not registered: {0}'.format(error))
