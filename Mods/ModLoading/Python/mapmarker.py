# -*- coding: utf-8 -*-
"""mapmarker - maintain a DA_MapMarker_V2 from the maps in its folder.

A map marker lists the BASE maps a mod ships. Maps that other maps stream in as
sublevels ("layers") must not be listed. Everything here works on package names
from the Asset Registry: NO map is ever loaded (loading a map also compiles every
Blueprint it depends on, and makes the editor lag).

Marker properties:
    Maps                 array F_ShippedMap            one row per shipped map (see below); driven via the
                                                       C++ helper unreal.ArgonMapMarkerLib (4.25 Python cannot
                                                       construct/append the Blueprint struct itself)
    IgnoredMaps          set  path                     base maps deliberately NOT shipped
    ExtraRequiredMaps    set  path                     cooked too, though no shipped map streams them in
    ShippedMapLayers     set  path                     DERIVED: sublevels shipped + extra maps need
    DefaultLoadingScreen soft Texture2D                fallback loading screen
    ExcludeFolders       array string                  folder names to skip when scanning
    ExcludeNamePatterns  array string                  map-name globs to skip (case-insensitive, e.g. *_bak)
    Notes                string                        free text for the owner
Each F_ShippedMap row = {Map (SoftObjectPath), State (E_MapShipState: Shipped|Held), LoadingScreen
(TSoftObjectPtr<Texture2D>), MapName, MapDescription (String), GamemodeType, AttackingFaction (int)}. A Held
row keeps its data but is excluded from the cook. The four summary fields (MapName/MapDescription/GamemodeType/
AttackingFaction) are auto-filled from the map's Asset Registry tags; `validate` flags a map with no tags
(re-save it) and a row whose stored summary has drifted from the live tags (refresh). The Map key is an
untyped FSoftObjectPath so Python/C++ never has to load the world. Path set properties still load nothing.
ExcludeFolders empty = the defaults (old, backup, reference); fill it to replace them.
ExcludeNamePatterns always adds to whichever folders apply.

Actions (editor):
    populate_shipped(marker)   add base maps that are neither shipped, ignored nor extra
    ignore_unused(marker)      put base maps that are not shipped into IgnoredMaps
    update_layers(marker)      recompute ShippedMapLayers
    required_maps(marker)      what a cook must include: shipped + extra + layers
    validate(marker)           read-only audit, returns the problems found
    refresh_summaries(marker)  re-pull every row's summary (name/desc/gamemode/faction) from the AR
    clear_maps(marker)         empty shipped / ignored / layers (keeps the settings)
    find_markers(folder)       the map markers below a folder
    cook_plan(folder)          what the cooker should cook for a mod folder (None = no marker)

Pure logic (find_base_maps, pending, layers_closure, audit_findings) is unit-tested
without the editor.
"""
from __future__ import print_function

import fnmatch

MAPS_PROPERTY = 'Maps'  # TArray<F_ShippedMap>, driven via unreal.ArgonMapMarkerLib (C++ editor helper)
IGNORED_PROPERTY = 'IgnoredMaps'
LAYERS_PROPERTY = 'ShippedMapLayers'
EXTRA_PROPERTY = 'ExtraRequiredMaps'
EXCLUDE_FOLDERS_PROPERTY = 'ExcludeFolders'
EXCLUDE_PATTERNS_PROPERTY = 'ExcludeNamePatterns'
NOTES_PROPERTY = 'Notes'
# Asset Registry map-summary tags (emitted by the TBL WITH_EDITOR writer; read without loading a map).
AR_NAME_TAG = 'MapNameSource'
AR_DESC_TAG = 'MapDescriptionSource'
AR_GAMEMODE_TAG = 'GamemodeType'
AR_FACTION_TAG = 'AttackingFaction'
# Folder names (case-insensitive) that hold backups / references, not shipped maps.
DEFAULT_EXCLUDE_DIRS = ('old', 'backup', 'reference')


# -- pure logic ----------------------------------------------------------------

def _excluded(package, root, exclude_dirs, exclude_patterns=()):
    """True if `package` sits below an excluded folder, or its name matches an excluded glob."""
    rel = package[len(root):].strip('/') if root and package.startswith(root) else package.strip('/')
    parts = rel.split('/')
    if exclude_dirs:
        folders = [part.lower() for part in parts[:-1]]
        if any(name.lower() in folders for name in exclude_dirs):
            return True
    name = parts[-1].lower()
    return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in (exclude_patterns or ()))


def find_base_maps(references, exclude_dirs=(), root=None, exclude_patterns=()):
    """Split maps into standalone base maps and layers.

    references: {package: iterable of packages it references} for every map in
    scope. A map is a LAYER when another in-scope map references it; the rest are
    BASE maps. Excluded maps (folder name or name glob) are ignored entirely
    (neither candidates nor referrers). Maps that only reference each other in a
    cycle would vanish, so they are returned as base maps and listed in `cyclic`.

    Returns {'base': [...], 'layers': {layer: [referrers]}, 'excluded': [...],
    'cyclic': [...]}, all sorted.
    """
    excluded = sorted(p for p in references if _excluded(p, root, exclude_dirs, exclude_patterns))
    skip = set(excluded)
    candidates = sorted(p for p in references if p not in skip)
    cset = set(candidates)
    referrers = {}
    for src in candidates:
        for dst in references[src]:
            if dst in cset and dst != src:
                referrers.setdefault(dst, set()).add(src)
    base = [p for p in candidates if p not in referrers]
    reached, queue = set(base), list(base)
    while queue:
        cur = queue.pop()
        for dst in references[cur]:
            if dst in cset and dst not in reached:
                reached.add(dst)
                queue.append(dst)
    cyclic = [p for p in candidates if p not in reached]
    return {
        'base': sorted(base + cyclic),
        'layers': dict((p, sorted(referrers[p])) for p in candidates if p in referrers and p in reached),
        'excluded': excluded,
        'cyclic': cyclic,
    }


def pending(base, shipped, ignored, extra=()):
    """Base maps that are neither shipped, ignored nor extra, in `base` order.

    Used by BOTH populate (these get shipped) and ignore-unused (these get ignored):
    the user shipping, ignoring or marking a map as extra takes it off this list.
    """
    taken = set(shipped) | set(ignored) | set(extra)
    return [p for p in base if p not in taken]


def layers_closure(roots, references_of):
    """Every map the `roots` stream in, directly or through other layers.

    references_of(package) -> iterable of the MAP packages it references. The roots
    themselves are not part of the result. Pure BFS, tolerant of cycles.
    """
    roots = list(roots)
    seen, queue = set(roots), list(roots)
    layers = set()
    while queue:
        cur = queue.pop()
        for dst in references_of(cur):
            if dst not in seen:
                seen.add(dst)
                layers.add(dst)
                queue.append(dst)
    return sorted(layers)


def audit_findings(shipped, ignored, extra, stored_layers, computed_layers, existing_worlds,
                   folder, pending_maps, references_of):
    """Problems with a marker, as readable lines (empty list = clean).

    shipped / ignored / extra / stored_layers: object paths. computed_layers: object paths
    of the layers the shipped + extra maps need. existing_worlds: set of map packages that
    exist. folder: the marker's folder. pending_maps: base maps not yet decided.
    references_of(package): map packages it references.
    """
    pkg = lambda p: p.split('.')[0]  # noqa: E731
    issues = []
    for label, items in (('Shipped', shipped), ('Ignored', ignored), ('Extra', extra)):
        stale = sorted(p for p in items if pkg(p) not in existing_worlds)
        for p in stale:
            issues.append('{0} map no longer exists: {1}'.format(label, p))
    for p in sorted(set(shipped) & set(ignored)):
        issues.append('Both shipped and ignored: {0}'.format(p))
    for p in sorted(set(shipped) & set(extra)):
        issues.append('Both shipped and extra (extra is redundant): {0}'.format(p))
    shipped_pkgs = set(pkg(p) for p in shipped)
    for p in sorted(shipped):
        others = shipped_pkgs - set([pkg(p)])
        carriers = sorted(o.split('/')[-1] for o in others if pkg(p) in layers_closure([o], references_of))
        if carriers:
            issues.append('Shipped but already a layer of {0}: {1}'.format(', '.join(carriers), p))
    prefix = folder.rstrip('/') + '/'
    for p in sorted(shipped):
        if not pkg(p).startswith(prefix):
            issues.append('Shipped map outside the marker folder: {0}'.format(p))
    missing = sorted(set(computed_layers) - set(stored_layers))
    extra_stored = sorted(set(stored_layers) - set(computed_layers))
    if missing or extra_stored:
        issues.append('ShippedMapLayers is out of date ({0} missing, {1} not needed) - run Update map layers'.format(
            len(missing), len(extra_stored)))
    if pending_maps:
        issues.append('{0} base map(s) are not shipped, ignored or extra yet (Populate or Ignore unused)'.format(
            len(pending_maps)))
    return issues


def merge_required(requireds):
    """Union several markers' required_maps() results into one cook plan (all package names).

    A map that is shipped or extra for one marker and only a layer for another stays shipped/extra.
    Returns {'shipped': [...], 'extra': [...], 'layers': [...], 'maps': [...]} - 'maps' is everything.
    """
    shipped, extra, layers = set(), set(), set()
    for required in requireds:
        shipped |= set(required.get('shipped', ()))
        extra |= set(required.get('extra', ()))
        layers |= set(required.get('layers', ()))
    extra -= shipped
    layers -= shipped | extra
    return {'shipped': sorted(shipped), 'extra': sorted(extra), 'layers': sorted(layers),
            'maps': sorted(shipped | extra | layers)}


def _package(object_path):
    return object_path.split('.')[0]


# -- editor side ---------------------------------------------------------------

def _registry():
    import unreal
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _dependency_options():
    import unreal
    options = unreal.AssetRegistryDependencyOptions()
    options.set_editor_property('include_hard_package_references', True)
    options.set_editor_property('include_soft_package_references', True)
    for name in ('include_searchable_names', 'include_soft_management_references',
                 'include_hard_management_references'):
        options.set_editor_property(name, False)
    return options


def _get(asset, property_name, default):
    """A marker property, or `default` when this marker predates the property."""
    try:
        return asset.get_editor_property(property_name)
    except Exception:
        return default


def exclusions(asset):
    """(folder names, name globs) from the marker.

    ExcludeFolders empty -> the defaults (old, backup, reference); fill it to replace them.
    ExcludeNamePatterns is purely additive.
    """
    folders = [str(x) for x in _get(asset, EXCLUDE_FOLDERS_PROPERTY, [])] or list(DEFAULT_EXCLUDE_DIRS)
    patterns = [str(x) for x in _get(asset, EXCLUDE_PATTERNS_PROPERTY, [])]
    return folders, patterns


def scan_folder(folder, exclude_dirs=DEFAULT_EXCLUDE_DIRS, exclude_patterns=()):
    """Asset Registry scan of `folder` (e.g. '/Game/Mods/AgMods/Mod'). Loads nothing.

    Returns find_base_maps() output plus 'object_paths' {package: 'pkg.Name'}.
    """
    registry = _registry()
    root = folder.rstrip('/') + '/'
    object_paths = {}
    for asset in registry.get_assets_by_path(folder, recursive=True):
        if str(asset.asset_class) == 'World':
            object_paths[str(asset.package_name)] = str(asset.object_path)
    options = _dependency_options()
    scope = set(object_paths)
    references = {}
    for package in object_paths:
        references[package] = set(str(d) for d in registry.get_dependencies(package, options)) & scope
    result = find_base_maps(references, exclude_dirs, root, exclude_patterns)
    result['object_paths'] = object_paths
    return result


def _load_marker(marker_path):
    import unreal
    package = _package(marker_path)
    asset = unreal.EditorAssetLibrary.load_asset(package)
    if asset is None:
        raise RuntimeError('marker not found: {0}'.format(marker_path))
    return package, asset


def read_paths(asset, property_name):
    """Object-path strings in a path-keyed map or a path set property (nothing is loaded)."""
    value = _get(asset, property_name, None)
    if value is None:
        return []
    items = value.keys() if hasattr(value, 'keys') else value
    return [item.to_tuple()[0] for item in items]


def _write(asset, property_name, object_paths):
    """Add object paths to a path SET property (IgnoredMaps/ExtraRequiredMaps/ShippedMapLayers)."""
    import unreal
    value = asset.get_editor_property(property_name)
    if hasattr(value, 'keys'):
        for path in object_paths:
            value[unreal.SoftObjectPath(path)] = None
    else:
        for path in object_paths:
            value.add(unreal.SoftObjectPath(path))
    asset.set_editor_property(property_name, value)


# -- F_ShippedMap rows (via the C++ editor helper) -----------------------------

def _lib():
    """unreal.ArgonMapMarkerLib, or a clear error when the editor C++ module is not built.

    4.25 Python cannot construct a UserDefinedStruct, grow a struct array, or call a Blueprint
    function, so the Maps rows are read/written through the ArgonSDKTooling editor module.
    """
    import unreal
    lib = getattr(unreal, 'ArgonMapMarkerLib', None)
    if lib is None:
        raise RuntimeError(
            'unreal.ArgonMapMarkerLib not found - build the ArgonSDKTooling editor C++ module '
            '(plugin Source/ArgonSDKToolingEditor) and restart the editor.')
    return lib


def _soft_str(soft):
    """Object-path string from an unreal.SoftObjectPath (empty if unset)."""
    try:
        return soft.to_tuple()[0]
    except Exception:
        return ''


def summary_from_ar(object_path):
    """(name, description, gamemode, faction) from the map's Asset Registry tags; blanks/zeros if absent.

    Reads the TBL map-summary tags straight from the registry - no map is loaded. Maps saved before the
    TBL WITH_EDITOR writer existed carry no tags (name/description come back empty); validate flags those.
    """
    registry = _registry()
    datas = registry.get_assets_by_package_name(_package(object_path))
    if not datas:
        return ('', '', 0, 0)
    data = datas[0]

    def tag(name):
        try:
            value = data.get_tag_value(name)
        except Exception:
            value = None
        return '' if value is None else str(value)

    def as_int(text):
        text = text.strip()
        return int(text) if text.lstrip('-').isdigit() else 0

    return (tag(AR_NAME_TAG), tag(AR_DESC_TAG), as_int(tag(AR_GAMEMODE_TAG)), as_int(tag(AR_FACTION_TAG)))


def read_rows(asset):
    """Every Maps row as a dict (Held included): map, held, loading_screen, name, description, gamemode, faction."""
    lib = _lib()
    count = lib.get_shipped_map_count(asset)
    rows = []
    for i in range(count if count and count > 0 else 0):
        mp, held, ls, name, desc, gt, af = lib.get_shipped_map(asset, i)
        rows.append({'map': _soft_str(mp), 'held': bool(held), 'loading_screen': _soft_str(ls),
                     'name': str(name), 'description': str(desc), 'gamemode': int(gt), 'faction': int(af)})
    return rows


def read_shipped(asset):
    """Object-path strings of every Maps row (Held included)."""
    return [row['map'] for row in read_rows(asset) if row['map']]


def _add_rows(asset, object_paths, autofill=True):
    """Add a State=Shipped row for each object path (summary auto-filled from AR). Dedupes by Map."""
    import unreal
    lib = _lib()
    for path in object_paths:
        name, desc, gamemode, faction = summary_from_ar(path) if autofill else ('', '', 0, 0)
        lib.add_or_update_shipped_map(asset, unreal.SoftObjectPath(path), False,
                                      unreal.SoftObjectPath(''), name, desc, gamemode, faction)


def refresh_summaries(marker_path, dry_run=False, save=True):
    """Re-pull every row's summary fields from the Asset Registry (preserves State + LoadingScreen).

    Returns {'updated': [...], 'missing': [...]} - 'missing' are maps with no AR tags (re-save them).
    """
    import unreal
    package, asset = _load_marker(marker_path)
    lib = _lib()
    updated, missing = [], []
    for row in read_rows(asset):
        if not row['map']:
            continue
        name, desc, gamemode, faction = summary_from_ar(row['map'])
        if not name and not desc:
            missing.append(row['map'])
            continue
        if (name, desc, gamemode, faction) != (row['name'], row['description'], row['gamemode'], row['faction']):
            if not dry_run:
                lib.set_shipped_map_summary(asset, unreal.SoftObjectPath(row['map']), name, desc, gamemode, faction)
            updated.append(row['map'])
    if updated and not dry_run and save:
        _save(asset)
    return {'updated': sorted(updated), 'missing': sorted(missing)}


def _save(asset):
    """Force-save the marker.

    Setting a property from Python does NOT mark the package dirty, and
    save_loaded_asset() defaults to only_if_is_dirty=True - it would silently write
    nothing. Pass False, and fail loudly if the save is refused.
    """
    import unreal
    asset.modify()
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset, False):
        raise RuntimeError('could not save {0}'.format(asset.get_path_name()))


def _pending(marker_path, folder, exclude_dirs):
    package, asset = _load_marker(marker_path)
    folders, patterns = exclusions(asset)
    if exclude_dirs is not None:
        folders = list(exclude_dirs)
    scan = scan_folder(folder or package.rsplit('/', 1)[0], folders, patterns)
    shipped = read_shipped(asset)
    ignored = read_paths(asset, IGNORED_PROPERTY)
    extra = read_paths(asset, EXTRA_PROPERTY)
    base = [scan['object_paths'][p] for p in scan['base']]
    todo = pending(base, shipped, ignored, extra)
    return asset, scan, shipped, ignored, todo


def populate_shipped(marker_path, folder=None, exclude_dirs=None, dry_run=False, save=True):
    """Add base maps that are neither shipped, ignored nor extra to ShippedMapPaths.

    Never removes or reorders existing entries or their loading screens. Reports
    entries whose map no longer exists ('stale') and maps listed as both shipped
    and ignored ('conflicts'). exclude_dirs=None uses the marker's own exclude fields.
    """
    asset, scan, shipped, ignored, todo = _pending(marker_path, folder, exclude_dirs)
    if todo and not dry_run:
        _add_rows(asset, todo)
        if save:
            _save(asset)
    known = set(shipped)
    return {'added': todo, 'kept': len(known), 'ignored': len(ignored),
            'stale': sorted(p for p in known if _package(p) not in scan['object_paths']),
            'conflicts': sorted(known & set(ignored)),
            'layers': scan['layers'], 'excluded': scan['excluded'], 'cyclic': scan['cyclic']}


# Backwards compatible name (the first version of the menu used it).
init_marker = populate_shipped


def ignore_unused(marker_path, folder=None, exclude_dirs=None, dry_run=False, save=True):
    """Put base maps that are not shipped (and not already ignored/extra) into IgnoredMaps."""
    asset, scan, shipped, ignored, todo = _pending(marker_path, folder, exclude_dirs)
    if todo and not dry_run:
        _write(asset, IGNORED_PROPERTY, todo)
        if save:
            _save(asset)
    return {'ignored_added': todo, 'ignored_total': len(ignored) + len(todo), 'shipped': len(shipped)}


def _world_packages():
    return set(str(a.package_name) for a in _registry().get_assets_by_class('World'))


def _references_of_factory():
    registry = _registry()
    options = _dependency_options()
    worlds = _world_packages()
    cache = {}

    def references_of(package):
        if package not in cache:
            cache[package] = [d for d in set(str(x) for x in registry.get_dependencies(package, options))
                              if d in worlds and d != package]
        return cache[package]
    return references_of, worlds


def required_maps(marker_path):
    """What a cook of this marker must include (packages, registry only, nothing loaded).

    {'shipped': [...], 'extra': [...], 'layers': [...]}. Layers are the sublevels the
    shipped AND extra maps stream in, found project-wide (not just in the marker's folder).
    """
    _package_name, asset = _load_marker(marker_path)
    # Held rows keep their data but are excluded from the cook.
    shipped = sorted(set(_package(r['map']) for r in read_rows(asset) if r['map'] and not r['held']))
    extra = sorted(set(_package(p) for p in read_paths(asset, EXTRA_PROPERTY)) - set(shipped))
    references_of, _worlds = _references_of_factory()
    roots = set(shipped) | set(extra)
    return {'shipped': shipped, 'extra': extra,
            'layers': [p for p in layers_closure(sorted(roots), references_of) if p not in roots]}


def _object_path_for(registry, package):
    found = [str(a.object_path) for a in registry.get_assets_by_package_name(package)
             if str(a.asset_class) == 'World']
    return found[0] if found else '{0}.{1}'.format(package, package.rsplit('/', 1)[-1])


def update_layers(marker_path, dry_run=False, save=True):
    """Recompute ShippedMapLayers (a derived set) from ShippedMapPaths + ExtraRequiredMaps."""
    import unreal
    package, asset = _load_marker(marker_path)
    needed = required_maps(marker_path)
    registry = _registry()
    after = set(_object_path_for(registry, pkg) for pkg in needed['layers'])
    before = set(read_paths(asset, LAYERS_PROPERTY))
    if not dry_run and before != after:
        value = asset.get_editor_property(LAYERS_PROPERTY)
        value.clear()
        for path in sorted(after):
            value.add(unreal.SoftObjectPath(path))
        asset.set_editor_property(LAYERS_PROPERTY, value)
        if save:
            _save(asset)
    return {'shipped': len(needed['shipped']), 'extra': len(needed['extra']), 'layers': len(after),
            'added': sorted(after - before), 'removed': sorted(before - after)}


def validate(marker_path, folder=None):
    """Read-only audit of a marker. Returns {'issues': [...], 'ok': bool, 'counts': {...}}."""
    package, asset = _load_marker(marker_path)
    folder = folder or package.rsplit('/', 1)[0]
    folders, patterns = exclusions(asset)
    scan = scan_folder(folder, folders, patterns)
    rows = read_rows(asset)
    shipped = [row['map'] for row in rows if row['map']]
    ignored = read_paths(asset, IGNORED_PROPERTY)
    extra = read_paths(asset, EXTRA_PROPERTY)
    stored = read_paths(asset, LAYERS_PROPERTY)
    references_of, worlds = _references_of_factory()
    registry = _registry()
    needed = required_maps(marker_path)
    computed = [_object_path_for(registry, p) for p in needed['layers']]
    base = [scan['object_paths'][p] for p in scan['base']]
    issues = audit_findings(shipped, ignored, extra, stored, computed, worlds, folder,
                            pending(base, shipped, ignored, extra), references_of)
    # Map-summary (Asset Registry) checks, per existing row. A missing map is already
    # reported by audit_findings, so skip rows whose map does not exist.
    for row in rows:
        pkg = _package(row['map'])
        if pkg not in worlds:
            continue
        name, desc, gamemode, faction = summary_from_ar(row['map'])
        short = pkg.split('/')[-1]
        if not name and not desc:
            issues.append('No map summary in the Asset Registry for {0} - open and re-save the map'.format(short))
        elif (row['name'], row['description'], row['gamemode'], row['faction']) != (name, desc, gamemode, faction):
            issues.append('Marker summary out of date for {0} - refresh (AR: name={1!r}, gamemode={2}, faction={3})'.format(
                short, name, gamemode, faction))
    return {'issues': issues, 'ok': not issues,
            'counts': {'shipped': len(rows), 'held': len([r for r in rows if r['held']]),
                       'ignored': len(ignored), 'extra': len(extra), 'layers': len(stored),
                       'base_in_folder': len(base), 'excluded': len(scan['excluded'])}}


def clear_maps(marker_path, dry_run=False, save=True):
    """Empty Maps, IgnoredMaps and ShippedMapLayers (the lists a scan fills).

    Keeps the settings: ExtraRequiredMaps, DefaultLoadingScreen, ExcludeFolders, ExcludeNamePatterns, Notes.
    Returns the counts removed; 'with_loading_screen' says how many rows had a custom loading screen
    (lost by clearing, so callers should confirm first).
    """
    import unreal
    package, asset = _load_marker(marker_path)
    rows = read_rows(asset)
    counts = {'shipped': len(rows),
              'ignored': len(read_paths(asset, IGNORED_PROPERTY)),
              'layers': len(read_paths(asset, LAYERS_PROPERTY)),
              'with_loading_screen': len([r for r in rows if r['loading_screen']])}
    if not dry_run and (counts['shipped'] or counts['ignored'] or counts['layers']):
        lib = _lib()
        for row in rows:
            lib.remove_shipped_map(asset, unreal.SoftObjectPath(row['map']))
        for name in (IGNORED_PROPERTY, LAYERS_PROPERTY):
            value = _get(asset, name, None)
            if value is not None:
                value.clear()
                asset.set_editor_property(name, value)
        if save:
            _save(asset)
    return counts


MARKER_CLASS = '/Game/Mods/ArgonSDK/Mods/ModLoading/v2/DataAssets/DA_MapMarker_V2.DA_MapMarker_V2_C'
DEFAULT_MARKER_NAME = 'MapMarker'


def create_map_marker(folder, name=DEFAULT_MARKER_NAME, populate=True, class_path=MARKER_CLASS):
    """Create a map marker `<folder>/<name>` and return (cdo, asset), like modtools.create_mod_marker.

    populate=True adds the maps found in `folder` (and below) to ShippedMapPaths - the same as the Populate action
    (base maps only; sublevels/layers are worked out; the marker's exclude settings apply). populate=False leaves
    the marker empty. An existing asset of that name is reused (and populated when asked); nothing is removed.
    The result is saved. Returns (cdo, asset); populate's report is in `asset`'s properties, or call populate_shipped.
    """
    import unreal
    folder = folder.rstrip('/')
    package = '{0}/{1}'.format(folder, name)
    asset = unreal.EditorAssetLibrary.load_asset(package) if unreal.EditorAssetLibrary.does_asset_exist(package) else None
    if asset is None:
        cls = unreal.load_object(None, class_path)
        if cls is None:
            raise RuntimeError('map marker class not found: {0}'.format(class_path))
        factory = unreal.DataAssetFactory()
        factory.set_editor_property('DataAssetClass', cls)
        asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.DataAsset, factory)
        if asset is None:
            raise RuntimeError('could not create a map marker in {0}'.format(folder))
        _save(asset)
    elif not _is_marker(asset):
        raise RuntimeError('{0} exists but is not a map marker'.format(package))
    if populate:
        populate_shipped(package, folder)
        asset = unreal.EditorAssetLibrary.load_asset(package)
    return unreal.get_default_object(asset.get_class()), asset


def _is_marker(asset):
    try:
        asset.get_editor_property(MAPS_PROPERTY)
        return True
    except Exception:
        return False


def find_markers(folder):
    """Package paths of the map markers below `folder` (registry scan; loads only data-asset instances).

    Markers are instances of DA_MapMarker_V2 or a Blueprint subclass of it, so their asset class
    is '<Name>_C'; each such instance is probed for the marker's ShippedMapPaths property.
    """
    import unreal
    found = []
    for asset in _registry().get_assets_by_path(folder, recursive=True):
        if not str(asset.asset_class).endswith('_C'):
            continue
        obj = unreal.EditorAssetLibrary.load_asset(str(asset.package_name))
        if obj is not None and _is_marker(obj):
            found.append(str(asset.package_name))
    return sorted(found)


def cook_plan(folder):
    """The cooker's map plan for a mod folder, or None when it has no map marker.

    {'markers': [...], 'shipped': [...], 'extra': [...], 'layers': [...], 'maps': [...]} - package
    names, registry only, nothing loaded. With several markers the plan is their union.
    """
    markers = find_markers(folder)
    if not markers:
        return None
    plan = merge_required([required_maps(marker) for marker in markers])
    plan['markers'] = markers
    return plan
