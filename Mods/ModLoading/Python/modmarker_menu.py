# -*- coding: utf-8 -*-
"""modmarker_menu - Content Browser right-click and marker-editor toolbar actions for DA_ModMarker_V2.

Built on the map marker menu's plumbing (message boxes, the on-screen note, the shared Popups toggle):
the entries only appear when exactly one V2 mod marker is selected (right-click) or was selected when its
editor opened (toolbar). Registered by mapmarker_menu.register(), so the existing startup line is enough.

Marker actions: Refresh from folder, Validate, Preview pak plan, Scaffold nested markers, Migrate V1 markers below.
Any other asset in a mod folder without a V2 marker also offers "Create mod marker" (4.25 Python cannot reach the
folder context menu).
Nothing here moves, renames or deletes an existing asset.
"""
import unreal

import mapmarker_menu as base
import modmarker

SECTION = 'ArgonModMarker'
TOOLBAR_SECTION = 'ArgonModMarkerToolbar'


def _is_mod_marker(asset):
    try:
        asset.get_editor_property(modmarker.P_ID)
        return True
    except Exception:
        return False


def _folder(package_path):
    return modmarker.marker_dir(package_path.split('.')[0])


def run_refresh(package_path):
    """Menu: make this marker match its folder - ModId (when empty), FormatVersion, ModVersion from mod.json, new mod
    actor classes added, classes now owned by a nested marker removed."""
    r = base._run('Refresh mod marker', lambda: modmarker.refresh_marker(package_path))
    if r is None:
        return
    parts = ['Added {0} mod actor class(es)'.format(len(r['added_classes']))]
    if r['removed_classes']:
        parts.append('removed {0} that belong to nested markers'.format(len(r['removed_classes'])))
    if r['id_set']:
        parts.append('ModId set from the {0}'.format('mod.json name' if r.get('id_source') == 'mod.json' else 'folder name'))
    if r['version_set']:
        parts.append('ModVersion set to {0} from mod.json'.format(r['version_set']))
    base._say('Refresh mod marker', ['; '.join(parts) + '.'] + ['added ' + c for c in r['added_classes']]
              + ['removed ' + c for c in r['removed_classes']])


run_init = run_refresh  # first version's name


def run_create(package_path):
    """Menu (any asset in a mod folder without a V2 marker): create the marker, populated from V1 or the folder."""
    r = base._run('Create mod marker', lambda: modmarker.create_v2_for(_folder(package_path)))
    if r is None:
        return
    where = r['marker'].split('/')[-2] + '/' + r['marker'].split('/')[-1]
    if not r['created']:
        base._say('Create mod marker', ['{0} already exists; refreshed it ({1} mod actor class(es)).'.format(where, r['classes'])])
        return
    base._say('Create mod marker', ['Created {0} from {1}: {2} mod actor class(es){3}.'.format(
        where, 'the V1 marker' if r['source'] == 'v1' else 'the folder', r['classes'],
        ', version ' + r['version'] if r['version'] else '') + (
        ' {0} V1 custom object(s) were not migrated (unused).'.format(r['dropped_objects']) if r.get('dropped_objects') else '')])


def run_mod_json(package_path):
    """Menu: create mod.json in this marker's folder when it has none (never overwrites)."""
    folder = _folder(package_path)
    r = base._run('Create mod.json', lambda: modmarker.create_mod_json(folder))
    if r is None:
        return
    if r['created']:
        base._say('Create mod.json', ['Created mod.json for "{0}" (version 0.1.0). Fill in repo_url, description, authors and tags.'.format(r['name'])])
    else:
        base._say('Create mod.json', ['{0} already has a mod.json; left untouched.'.format(folder.split('/')[-1])])


def _bump(package_path, part):
    r = base._run('Bump version', lambda: modmarker.bump_version(_folder(package_path), part))
    if r is None:
        return
    name = r['path'].replace('\\', '/').split('/')[-2]
    base._say('Bump version', ['{0}: {1} -> {2} (mod.json); re-stamped {3} marker(s).'.format(
        name, r['old'] or 'no version', r['new'], len(r['stamped']))])


def run_bump_patch(package_path):
    """Menu: 1.2.3 -> 1.2.4 in the applicable mod.json, then re-stamp the markers."""
    _bump(package_path, 'patch')


def run_bump_minor(package_path):
    """Menu: 1.2.3 -> 1.3.0."""
    _bump(package_path, 'minor')


def run_bump_major(package_path):
    """Menu: 1.2.3 -> 2.0.0."""
    _bump(package_path, 'major')


def run_validate(package_path):
    """Menu: read-only audit of this marker's folder tree (including markers it references)."""
    r = base._run('Validate mod markers', lambda: modmarker.validate(_folder(package_path)))
    if r is None:
        return
    for line in r['issues']:
        unreal.log_warning('[modmarker] ' + line)
    if r['ok']:
        base._say('Validate mod markers', ['No problems found.', '{0} marker(s) checked.'.format(r['markers'])], always=True)
    else:
        base._say('Validate mod markers', ['{0} problem(s) in {1} marker(s):'.format(len(r['issues']), r['markers'])]
                  + base._short(r['issues'], 15), always=True)


def run_plan(package_path):
    """Menu: which paks this marker tree produces and which markers go into each (nothing is built)."""
    r = base._run('Pak plan', lambda: modmarker.plan(_folder(package_path)))
    if r is None:
        return
    name = lambda m: m.split('/')[-2]  # noqa: E731
    lines = []
    for pak in r['paks']:
        replaces = len(r.get('replaces', {}).get(pak['stem'], []))
        lines.append('{0}.pak ({1}, version {2}{3}): {4}'.format(
            pak['stem'], 'separate' if pak['separate'] else 'merged',
            r['versions'].get(pak['root']) or 'none', ', replaces {0} base-game asset(s)'.format(replaces) if replaces else '',
            ', '.join(name(m) for m in pak['markers'])))
    lines += ['WARNING: ' + w for w in r['warnings']]
    if len(r['paks']) > 1:
        lines.append('Note: a mod registry release holds exactly one .pak - each of these ships separately.')
    base._say('Pak plan', lines or ['No markers found.'], always=True)


def run_scaffold(package_path):
    """Menu: create a V2 marker in every mod folder under <folder>/Mods that has none (asks first)."""
    folder = _folder(package_path)
    plan = base._run('Scaffold nested markers', lambda: modmarker.scaffold_nested(folder, dry_run=True))
    if plan is None:
        return
    if not plan['created']:
        base._say('Scaffold nested markers', ['Every mod folder under {0}/Mods already has a marker.'.format(folder)])
        return
    names = ', '.join(p.split('/')[-1] for p in plan['created'])
    answer = base._message_box('Scaffold nested markers', u'Create a mod marker in {0} folder(s)?\n{1}\n\nExisting assets are not touched.'.format(
        len(plan['created']), names), yes_no=True)
    if answer is not True:
        base._log('scaffold cancelled' if answer is False else 'scaffold needs a dialog to confirm and none could be shown')
        return
    done = base._run('Scaffold nested markers', lambda: modmarker.scaffold_nested(folder))
    if done is not None:
        base._say('Scaffold nested markers', ['Created {0} marker(s): {1}'.format(len(done['created']), names)])


def run_migrate(package_path):
    """Menu: create a populated V2 marker for every folder at or below this marker's folder that only has a V1 marker."""
    folder = _folder(package_path)
    plan = base._run('Migrate V1 markers', lambda: modmarker.migrate_v1_tree(folder, dry_run=True))
    if plan is None:
        return
    if not plan['migrated']:
        base._say('Migrate V1 markers', ['No folder below {0} has only a V1 marker.'.format(folder.split('/')[-1])])
        return
    names = ', '.join(f.split('/')[-1] for f in plan['migrated'])
    answer = base._message_box('Migrate V1 markers', u'Create a V2 marker (populated from its V1 marker) in {0} folder(s)?\n{1}\n\nThe V1 markers are not changed.'.format(
        len(plan['migrated']), names), yes_no=True)
    if answer is not True:
        base._log('migrate cancelled' if answer is False else 'migrate needs a dialog to confirm and none could be shown')
        return
    done = base._run('Migrate V1 markers', lambda: modmarker.migrate_v1_tree(folder))
    if done is not None:
        base._say('Migrate V1 markers', ['Created {0} V2 marker(s): {1}'.format(len(done['migrated']), names)])


ACTIONS = (
    # (menu entry name, label, function, tooltip, EditorStyle icon, toolbar label)
    ('ArgonModMarkerRefresh', 'Refresh mod marker from folder', 'run_refresh',
     'Make this marker match its folder: ModId, FormatVersion, ModVersion from mod.json, new mod actor classes (classes of nested markers are removed here).',
     'LevelEditor.Create', 'Refresh'),
    ('ArgonModMarkerValidate', 'Validate mod markers', 'run_validate',
     'Read-only audit of this marker tree: ids, versions, bundled markers, missing classes/assets, cycles.',
     'LevelEditor.MapCheck', 'Validate'),
    ('ArgonModMarkerPlan', 'Preview pak plan', 'run_plan',
     'Show which paks this tree produces and which markers go into each. Builds nothing.',
     'LevelEditor.WorldProperties', 'Pak plan'),
    ('ArgonModMarkerScaffold', 'Scaffold nested markers', 'run_scaffold',
     'Create a marker in every mod folder under <folder>/Mods that has none. Asks first; existing assets are untouched.',
     'BlueprintEditor.AddNewFunction', 'Scaffold'),
    ('ArgonModMarkerBumpPatch', 'Bump version (patch)', 'run_bump_patch',
     'Bump the version in the applicable mod.json (1.2.3 -> 1.2.4) and re-stamp the markers. A nested marker needs its own mod.json.',
     'LevelEditor.OpenMarketplace', 'Bump'),
    ('ArgonModMarkerBumpMinor', 'Bump version (minor)', 'run_bump_minor',
     'Bump the version in the applicable mod.json (1.2.3 -> 1.3.0) and re-stamp the markers.',
     'LevelEditor.OpenMarketplace', 'Bump minor'),
    ('ArgonModMarkerBumpMajor', 'Bump version (major)', 'run_bump_major',
     'Bump the version in the applicable mod.json (1.2.3 -> 2.0.0) and re-stamp the markers.',
     'LevelEditor.OpenMarketplace', 'Bump major'),
    ('ArgonModMarkerModJson', 'Create mod.json (if missing)', 'run_mod_json',
     'Create mod.json in this marker\'s folder when it has none: name from ModId, version 0.1.0, registry-shaped. Never overwrites.',
     'LevelEditor.GameSettings', 'mod.json'),
    ('ArgonModMarkerMigrate', 'Migrate V1 markers below', 'run_migrate',
     'Create a populated V2 marker for every folder below this one that only has a V1 marker. Asks first; V1 markers are left untouched.',
     'AssetEditor.ReimportAsset', 'Migrate V1'),
)


@unreal.uclass()
class ModMarkerMenuSection(unreal.ToolMenuSectionDynamic):
    @unreal.ufunction(override=True)
    def construct_sections(self, menu, context):
        try:
            asset_context = context.find_by_class(unreal.ContentBrowserAssetContextMenuContext)
            if not asset_context:
                return
            selected = asset_context.get_selected_objects()
            if len(selected) != 1:
                return
            package = selected[0].get_outermost().get_name()
            if _is_mod_marker(selected[0]):
                menu.add_section(section_name=SECTION, label='Mod marker')
                for name, label, function, tip, icon, _short in ACTIONS:
                    entry = unreal.ToolMenuEntry(name=name, type=unreal.MultiBlockType.MENU_ENTRY)
                    entry.set_label(label)
                    entry.set_tool_tip(tip)
                    base._set_icon(entry, icon)
                    entry.set_string_command(
                        type=unreal.ToolMenuStringCommandType.PYTHON, custom_type=unreal.Name(''),
                        string='import modmarker_menu; modmarker_menu.{0}(r"{1}")'.format(function, package))
                    menu.add_menu_entry(SECTION, entry)
                return
            # not a marker: offer to create one for this asset's folder (4.25 Python cannot reach the folder menu)
            folder = modmarker.marker_dir(package)
            if not folder.startswith('/Game/Mods/'):
                return
            state = modmarker.status(folder)['state']
            if state == 'v2':
                return
            entry = unreal.ToolMenuEntry(name='ArgonModMarkerCreate', type=unreal.MultiBlockType.MENU_ENTRY)
            entry.set_label('Create mod marker from V1 marker (this folder)' if state == 'v1_only' else 'Create mod marker (this folder)')
            entry.set_tool_tip('Create the V2 mod marker for {0}, populated automatically (from its V1 marker when there is one). '
                               'Adds only; nothing existing is changed.'.format(folder.split('/')[-1]))
            base._set_icon(entry, 'LevelEditor.Create')
            entry.set_string_command(
                type=unreal.ToolMenuStringCommandType.PYTHON, custom_type=unreal.Name(''),
                string='import modmarker_menu; modmarker_menu.run_create(r"{0}")'.format(package))
            menu.add_section(section_name=SECTION, label='Mod marker')
            menu.add_menu_entry(SECTION, entry)
        except Exception as error:  # noqa: BLE001
            unreal.log_error('[modmarker] menu construction failed: {0}'.format(error))


@unreal.uclass()
class ModMarkerToolbarSection(unreal.ToolMenuSectionDynamic):
    """Toolbar buttons for the mod marker's editor - added only when the selected asset (the one just
    opened) is a V2 mod marker; same selection-at-open rule as the map marker toolbar."""

    @unreal.ufunction(override=True)
    def construct_sections(self, menu, context):
        try:
            selected = unreal.EditorUtilityLibrary.get_selected_assets()
            if len(selected) != 1 or not _is_mod_marker(selected[0]):
                return
            package = selected[0].get_outermost().get_name()
            marker = selected[0].get_name()
            menu.add_section(section_name=TOOLBAR_SECTION, label='')
            for name, label, function, tip, icon, short_label in ACTIONS:
                if function in ('run_bump_minor', 'run_bump_major'):
                    continue  # patch is the toolbar button; minor/major live in the right-click menu
                entry = unreal.ToolMenuEntry(name=name + 'Tb', type=unreal.MultiBlockType.TOOL_BAR_BUTTON)
                entry.set_label(short_label)
                base._set_icon(entry, icon, toolbar=True)
                entry.set_tool_tip('{0} ({1})'.format(tip, marker))
                entry.set_string_command(
                    type=unreal.ToolMenuStringCommandType.PYTHON, custom_type=unreal.Name(''),
                    string='import modmarker_menu; modmarker_menu.{0}(r"{1}")'.format(function, package))
                menu.add_menu_entry(TOOLBAR_SECTION, entry)
            menu.add_menu_entry_object(base._POPUP_TOGGLE)
            base._log('mod marker toolbar buttons added for ' + package)
        except Exception as error:  # noqa: BLE001
            unreal.log_error('[modmarker] toolbar construction failed: {0}'.format(error))


try:
    _SECTION
except NameError:
    _SECTION = ModMarkerMenuSection()
try:
    _TOOLBAR_SECTION
except NameError:
    _TOOLBAR_SECTION = ModMarkerToolbarSection()


def register(toolbar=True):
    """Attach the mod marker sections to the context menu and (optionally) the asset editor toolbar. Idempotent."""
    tool_menus = unreal.ToolMenus.get()
    tool_menus.extend_menu(base.MENU_NAME).add_dynamic_section(SECTION, _SECTION)
    if toolbar:
        tool_menus.extend_menu(base.TOOLBAR_NAME).add_dynamic_section(TOOLBAR_SECTION, _TOOLBAR_SECTION)
    tool_menus.refresh_all_widgets()
    base._log('mod marker menus registered')
