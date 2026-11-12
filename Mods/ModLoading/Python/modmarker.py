# -*- coding: utf-8 -*-
"""modmarker - maintain DA_ModMarker_V2 mod markers (including nested ones).

A mod marker names a mod (ModId / ModVersion), lists the mod actor classes and extra assets it ships
(soft paths only - nothing is loaded), and can reference markers elsewhere. Markers nest by folder:
a marker's parent is the nearest marker above it in the folder tree; there is no parent field.

Properties of DA_ModMarker_V2, by Details category (path properties are FSoftObjectPath / FSoftClassPath
structs, because a typed soft pointer cannot be written from Python without loading the asset):
  Identity
    ModId             string     stable id = pak stem; filled from mod.json `name` (when pak-safe) else the folder name
    ModVersion        string     from the nearest mod.json `version`; empty = inherit the nearest ancestor's
    FormatVersion     int        marker format, 1
    Icon              soft tex   picture for the mod list
    ListTags          array      tags for the in-game list (independent of mod.json's tags)
  Content
    ModActorClasses   set        mod actor classes (soft class paths)
  (V1's CustomObjects - assets a pak replaces - was never used and is dropped; what a pak replaces is derived
  from its _Content folders, see replaced_assets)
  Packaging
    BundledMarkers    set        markers packaged into THIS pak, wherever they live (real paths kept)
    ExcludedPaths     array      patterns (relative to the folder) to skip when cooking / packing: prefixes, or * and ? wildcards
    OwnPak            bool       nested marker only: its own <ModId>.pak instead of merging into the parent's

Nothing here moves, renames or deletes an existing asset: the only assets created are markers named
`ModMarker`, and existing markers are only ever extended.

Pure logic (build_tree, effective_version, pak_plan, audit_markers, ...) is unit-tested without the editor.
"""
from __future__ import print_function

import re

SCHEMA_VERSION = 1
MARKER_NAME = 'ModMarker'   # the legacy asset name; new markers are '<Folder>_ModMarker' (marker_name)
V2_CLASS_NAME = 'DA_ModMarker_V2'
V2_INSTANCE_CLASS = 'DA_ModMarker_V2_C'
V1_INSTANCE_CLASS = 'DA_ModMarker_C'
MOD_BASE_CLASS = '/Game/Mods/ArgonSDK/Mods/ArgonSDKModBase.ArgonSDKModBase_C'

P_SCHEMA, P_ID, P_VERSION = 'FormatVersion', 'ModId', 'ModVersion'
P_CLASSES, P_REFS = 'ModActorClasses', 'BundledMarkers'
V1_CLASS_PACKAGE = '/Game/Mods/ArgonSDK/Mods/ModLoading/DA_ModMarker'
P_EXCLUDE, P_ICON, P_TAGS, P_SEPARATE = 'ExcludedPaths', 'Icon', 'ListTags', 'OwnPak'
P_BUNDLE_VERSION = 'ApplyVersionToBundled'   # optional bool: the top mod's version also goes onto the markers/actors packed with it
ACTOR_VERSION_PROPERTIES = ('ModVersion', 'mod_version')   # ArgonSDKModBase.ModVersion


# -- pure logic ----------------------------------------------------------------

def marker_name(folder):
    """Asset name for a new marker in `folder`: '<FolderName>_ModMarker', so it is easy to tell apart in the asset
    browser and in asset pickers. Markers are found by class, so existing 'ModMarker' assets stay valid."""
    return '{0}_{1}'.format(folder.rstrip('/').split('/')[-1], MARKER_NAME)


def marker_dir(package):
    """Folder (package path) a marker sits in, e.g. '/Game/A/B/ModMarker' -> '/Game/A/B'."""
    return package.rsplit('/', 1)[0]


def build_tree(markers):
    """Marker hierarchy derived from the folder tree.

    markers: iterable of marker package paths. A marker's parent is the marker in the closest ancestor
    folder (a marker in the SAME folder is a peer, not a parent). Returns
    {'dir': {m: folder}, 'parent': {m: parent or None}, 'children': {m: [..]}, 'roots': [..]}.
    """
    markers = sorted(set(markers))
    dirs = dict((m, marker_dir(m)) for m in markers)
    parent, children = {}, dict((m, []) for m in markers)
    for m in markers:
        best = None
        for other in markers:
            if other == m or not dirs[m].startswith(dirs[other] + '/'):
                continue
            if best is None or len(dirs[other]) > len(dirs[best]):
                best = other
        parent[m] = best
        if best is not None:
            children[best].append(m)
    return {'dir': dirs, 'parent': parent, 'children': children,
            'roots': [m for m in markers if parent[m] is None]}


def subtree(tree, marker):
    """The marker and all its descendants (deterministic order)."""
    out, stack = [], [marker]
    while stack:
        cur = stack.pop(0)
        out.append(cur)
        stack = sorted(tree['children'].get(cur, [])) + stack
    return out


def effective_version(marker, versions, tree):
    """ModVersion of `marker`; an empty one inherits the nearest ancestor's non-empty version."""
    cur = marker
    while cur is not None:
        if versions.get(cur):
            return versions[cur]
        cur = tree['parent'].get(cur)
    return ''


def owned_by(package, own_dir, other_dirs):
    """True if `package` is in `own_dir` but not in a deeper folder that belongs to another marker."""
    if not package.startswith(own_dir + '/'):
        return False
    return not any(d != own_dir and d.startswith(own_dir + '/') and package.startswith(d + '/')
                   for d in other_dirs)


def mod_json_field(folder, read_json, field, root='/Game', exact=False):
    """A string field (`version`, `name`) from a mod.json for a /Game/... folder, or None.

    read_json(folder) -> the parsed mod.json dict of that exact folder, or None. exact=True looks only at
    `folder`; otherwise the nearest mod.json walking up towards `root` wins (like deploy/version.py). A
    mod.json without a usable value counts as "none here" and, when walking up, is skipped.
    """
    cur = folder.rstrip('/')
    while cur and cur != root.rstrip('/') and cur.startswith(root.rstrip('/') + '/'):
        data = read_json(cur)
        if isinstance(data, dict):
            value = str(data.get(field, '')).strip()
            if value:
                return value
        if exact:
            return None
        cur = cur.rsplit('/', 1)[0]
    return None


def mod_json_version(folder, read_json, root='/Game', exact=False):
    """`version` from a mod.json (see mod_json_field)."""
    return mod_json_field(folder, read_json, 'version', root, exact)


_PAK_SAFE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def is_pak_safe(name):
    """True if `name` can be a pak file stem: letters, digits, '.', '_' and '-' only, no spaces or colons."""
    return bool(name) and bool(_PAK_SAFE.match(name))


def suggest_mod_id(folder, read_json, exact=False, root='/Game'):
    """(ModId, source): the mod.json `name` when it is pak-safe, otherwise the folder name.

    A root marker uses the nearest mod.json up the tree, a nested one (exact=True) only its own folder's.
    Names like "ArgonSDK: Example Mod" are display names and are not used as ids.
    """
    name = mod_json_field(folder, read_json, 'name', root, exact)
    if name and is_pak_safe(name):
        return name, 'mod.json'
    return folder.rstrip('/').split('/')[-1], 'folder'


def classify_v1_dependencies(deps, classes_by_package, parents, base, ignore=()):
    """Split a V1 marker's hard dependencies into (mod actor class paths, other packages) without loading anything.

    deps: dependency package names. classes_by_package: {package: [class paths in it]}. A dependency whose
    Blueprint classes derive from `base` is a mod actor (V1 ModActors key); everything else except `ignore`
    was a CustomObjects entry (not migrated).
    """
    classes, others = [], []
    for package in sorted(set(deps)):
        if package in ignore:
            continue
        found = [c for c in classes_by_package.get(package, []) if descends_from(parents, c, base)]
        if found:
            classes += found
        else:
            others.append(package)
    return sorted(classes), others


def content_target(package):
    """'/Game/X/_Content/A/B' -> '/Game/A/B': the base-game path an asset in a _Content folder replaces (else None)."""
    token = '/_Content/'
    index = package.find(token)
    return '/Game/' + package[index + len(token):] if index >= 0 else None


def plan_stamps(tree, versions, lookup):
    """{marker: new ModVersion} for the markers whose version should change from their mod.json.

    A ROOT marker takes the nearest mod.json up from its folder; a NESTED marker only one in its own folder
    (otherwise it keeps inheriting through the marker tree, so a changed root version never rewrites the
    nested markers). lookup(folder, exact) -> version or None. Markers already on the right value are left out.
    """
    changes = {}
    for marker, folder in sorted(tree['dir'].items()):
        wanted = lookup(folder, tree['parent'].get(marker) is not None)
        if wanted and versions.get(marker, '') != wanted:
            changes[marker] = wanted
    return changes


def plan_marker_versions(tree, versions, lookup, paks, bundle_flags, scope):
    """{marker: new ModVersion} for the markers in `scope` (and the members of flagged paks) whose version should change.

    Base rule (plan_stamps): a root marker takes the nearest mod.json version, a nested one only its own folder's.
    On top of that, a pak whose ROOT marker has ApplyVersionToBundled set forces the root's version (its mod.json
    version, else what it already has) onto every other marker packed into that pak - markers merged from below and
    bundled from other directories - whatever their own mod.json says. OwnPak markers are other paks' roots and keep
    their own version. `paks` is pak_plan()['paks']; bundle_flags {marker: bool}.
    """
    wanted = {}
    for marker in scope:
        found = lookup(tree['dir'][marker], tree['parent'].get(marker) is not None)
        if found:
            wanted[marker] = found
    claimed = set()
    for pak in paks:
        root = pak['root']
        if not bundle_flags.get(root):
            continue
        top = wanted.get(root) or effective_version(root, versions, tree)
        if not top:
            continue
        for member in pak['markers']:
            if member != root and member not in claimed:
                wanted[member] = top
                claimed.add(member)
    return dict((m, v) for m, v in wanted.items() if versions.get(m, '') != v)


def plan_actor_versions(tree, versions, classes, markers):
    """{mod actor class path: version}: each marker's ModActorClasses get that marker's effective version.

    `versions` already includes the planned marker changes; markers without any version (own or inherited) stamp nothing.
    """
    out = {}
    for marker in sorted(markers):
        version = effective_version(marker, versions, tree)
        if version:
            for class_path in classes.get(marker, []):
                out.setdefault(class_path, version)
    return out


def marker_state(v2_markers, v1_markers):
    """'v2' when the folder has a V2 marker, 'v1_only' when it only has a V1 one, else 'none'."""
    if v2_markers:
        return 'v2'
    return 'v1_only' if v1_markers else 'none'


def _pattern_regex(pattern):
    return re.compile('^' + ''.join('.*' if c == '*' else '.' if c == '?' else re.escape(c) for c in pattern) + '$')


def pattern_matches(rel_path, pattern):
    """True if `rel_path` (relative to the marker folder, no extension) is excluded by one ExcludedPaths pattern.

    No wildcard = path prefix ("Old/" = everything under Old/). With * (any characters incl. '/') or ? (one
    character) the pattern must match the whole path or one of its parent folders ("*_bak", "*/Backup").
    Case-insensitive. KEEP IN SYNC with ArgonSDK-Tooling deploy/markers.py (the cook applies the same rule).
    """
    rel = rel_path.strip('/').lower()
    pat = pattern.strip().replace('\\', '/').lstrip('/').lower()
    if not pat:
        return False
    if '*' not in pat and '?' not in pat:
        return rel.startswith(pat)
    regex = _pattern_regex(pat)
    parts = rel.split('/')
    return any(regex.match('/'.join(parts[:i])) for i in range(1, len(parts) + 1))


ARGON_MODS_REPO = 'https://github.com/Chiv2-Community/ArgonSDK-Mods'


def build_mod_json(name, version='0.1.0', ag_mod=False, authors=(), description='', repo_url='', tags=(), mod_type='Shared'):
    """An ordered mod.json dict in the shape the existing mod.json files use (see the mod registry).

    ag_mod=True (mods under /Game/Mods/AgMods) adds the `ag_mod` flag and the dependency on ArgonSDK-Mods that the
    existing AgMods mods declare. `version` is the field the V2 marker reads for ModVersion.
    """
    from collections import OrderedDict
    data = OrderedDict([('repo_url', repo_url), ('name', name), ('description', description), ('mod_type', mod_type),
                        ('authors', list(authors)),
                        ('dependencies', [OrderedDict([('repo_url', ARGON_MODS_REPO), ('version', '>=0.0.1')])] if ag_mod else [])])
    if ag_mod:
        data['ag_mod'] = True
    data['tags'] = list(tags)
    data['version'] = version
    return data


_SEMVER = re.compile(r'^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$')


def bump_semver(version, part='patch'):
    """'1.2.3' -> '1.2.4' (patch), '1.3.0' (minor) or '2.0.0' (major). A -prerelease/+build suffix is dropped."""
    match = _SEMVER.match(version.strip())
    if not match:
        raise ValueError('not a MAJOR.MINOR.PATCH version: {0!r}'.format(version))
    major, minor, patch = [int(x) for x in match.groups()]
    if part == 'major':
        return '{0}.0.0'.format(major + 1)
    if part == 'minor':
        return '{0}.{1}.0'.format(major, minor + 1)
    if part == 'patch':
        return '{0}.{1}.{2}'.format(major, minor, patch + 1)
    raise ValueError('part must be major, minor or patch: {0!r}'.format(part))


def _top_level_string_span(text, key):
    """(start, end) of the text of the string VALUE of a top-level `key` in a JSON object, or None.

    Walks the text tracking strings and nesting, so a "version" inside `dependencies` (or any nested object) is never
    mistaken for the top-level one. Only plain string values are found.
    """
    depth, i, n = 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            string = text[i + 1:j]
            if depth == 1 and string == key:
                k = j + 1
                while k < n and text[k] in ' \t\r\n':
                    k += 1
                if k < n and text[k] == ':':
                    k += 1
                    while k < n and text[k] in ' \t\r\n':
                        k += 1
                    if k < n and text[k] == '"':
                        m = k + 1
                        while m < n and text[m] != '"':
                            m += 2 if text[m] == '\\' else 1
                        return k + 1, m
            i = j + 1
            continue
        if c in '{[':
            depth += 1
        elif c in '}]':
            depth -= 1
        i += 1
    return None


def bump_version_text(text, part='patch'):
    """(new text, old version, new version): only the value of the TOP-LEVEL "version" field changes.

    Raises KeyError when there is no top-level "version" string (a nested one, e.g. a dependency's, does not count).
    """
    span = _top_level_string_span(text, 'version')
    if span is None:
        raise KeyError('version')
    old = text[span[0]:span[1]]
    new = bump_semver(old, part)
    return text[:span[0]] + new + text[span[1]:], old, new


def mod_json_folder(folder, isfile, exact=False, root='/Game'):
    """The folder whose mod.json applies to `folder`: itself when `isfile(folder)`, else (not exact) the nearest ancestor."""
    cur = folder.rstrip('/')
    while cur and cur != root.rstrip('/') and cur.startswith(root.rstrip('/') + '/'):
        if isfile(cur):
            return cur
        if exact:
            return None
        cur = cur.rsplit('/', 1)[0]
    return None


def class_owner(package, marker_dirs):
    """The marker whose folder most closely contains `package` (marker_dirs = {marker: folder}), or None."""
    best = None
    for marker, folder in marker_dirs.items():
        if package.startswith(folder + '/') and (best is None or len(folder) > len(marker_dirs[best])):
            best = marker
    return best


def nested_owned_classes(marker, classes, tree):
    """Entries of `classes` that live in the folder of a marker NESTED below `marker` (and so belong to that one)."""
    descendants = set(subtree(tree, marker)) - set([marker])
    dirs = dict((m, tree['dir'][m]) for m in descendants)
    out = {}
    for path in classes:
        owner = class_owner(path.split('.')[0], dirs)
        if owner is not None:
            out[path] = owner
    return out


def duplicate_ids(ids):
    """{mod_id: [markers]} for non-empty ModIds used by more than one marker."""
    seen = {}
    for marker, mod_id in ids.items():
        if mod_id:
            seen.setdefault(mod_id, []).append(marker)
    return dict((k, sorted(v)) for k, v in seen.items() if len(v) > 1)


def reference_cycles(refs):
    """Cycles in the BundledMarkers graph ({marker: [markers]}); each cycle is a list ending where it began."""
    cycles, state = [], {}

    def visit(node, path):
        state[node] = 1
        for nxt in sorted(refs.get(node, [])):
            if state.get(nxt) == 1:
                cycles.append(path[path.index(nxt):] + [nxt] if nxt in path else [node, nxt])
            elif state.get(nxt) is None:
                visit(nxt, path + [nxt])
        state[node] = 2

    for node in sorted(refs):
        if state.get(node) is None:
            visit(node, [node])
    return cycles


def pak_plan(tree, separate, refs, ids=None, scope=None):
    """Which markers end up in which pak.

    A pak is rooted at every root marker and at every marker with OwnPak. Its members are the root's
    subtree minus the subtrees of other separate markers, plus (transitively) the markers its members
    reference - those keep their own real paths - except referenced markers that are separate themselves.
    Returns {'paks': [{'root', 'stem', 'separate', 'markers'}], 'warnings': [..]}; the stem is the pak
    root's ModId (the folder name when empty). `scope` (a set of markers) limits which markers get a pak of their
    own; markers outside it (e.g. a referenced marker in another directory) only ever appear as merged content.
    """
    ids = ids or {}
    separate = set(m for m in separate if tree['parent'].get(m) is not None)  # OwnPak on a root is moot
    pak_roots = sorted((set(tree['roots']) | separate) & set(scope if scope is not None else tree['dir']))
    warnings, membership = [], {}

    def merged(root):
        members, stack = [], [root]
        while stack:
            cur = stack.pop(0)
            if cur in members:
                continue
            members.append(cur)
            stack = sorted(c for c in tree['children'].get(cur, []) if c not in separate) + stack
        return members

    paks = []
    for root in pak_roots:
        members = merged(root)
        queue = list(members)
        while queue:
            cur = queue.pop(0)
            for target in sorted(refs.get(cur, [])):
                if target in separate and target != root:
                    warnings.append('{0} references {1}, which has OwnPak: not merged into {2}'.format(
                        cur.split('/')[-2], target.split('/')[-2], root.split('/')[-2]))
                    continue
                if target not in members and target in tree['dir']:
                    for extra in merged(target):
                        if extra not in members:
                            members.append(extra)
                            queue.append(extra)
        stem = ids.get(root) or tree['dir'][root].split('/')[-1]
        paks.append({'root': root, 'stem': stem, 'separate': root in separate, 'markers': sorted(members)})
        for m in members:
            membership.setdefault(m, []).append(stem)
    for m, stems in sorted(membership.items()):
        if len(stems) > 1:
            warnings.append('{0} would be packaged into more than one pak: {1}'.format(
                m.split('/')[-2], ', '.join(sorted(stems))))
    return {'paks': paks, 'warnings': warnings}


def descends_from(parent_map, class_path, base_path):
    """True if class_path derives from base_path (not equal to it); parent_map = {class: parent class}."""
    seen, cur = set(), parent_map.get(class_path)
    while cur and cur not in seen:
        if cur == base_path:
            return True
        seen.add(cur)
        cur = parent_map.get(cur)
    return False


def class_from_tag(tag):
    """The path inside a registry tag like \"BlueprintGeneratedClass'/Game/X.X_C'\" ('' if none)."""
    if not tag or "'" not in tag:
        return ''
    return tag.split("'")[1]


def audit_markers(info, tree, exists, rooted=None, json_names=None, other_ids=None):
    """Problems across a set of markers, as readable lines (empty = clean).

    info: {marker: {'id','version','separate','refs','classes'}}. exists(path) -> bool says whether an asset path
    (object path) exists. `rooted` limits the 'root needs a version' check to those markers. json_names:
    {marker: the mod.json name that applies to it} for the ModId-vs-mod.json check. other_ids: {marker: ModId}
    of markers outside `info` (the rest of the project) for the uniqueness check. Optional per-marker facts
    in `info`: 'convention' (the folder-named mod actor class, which V1 loaded by naming convention),
    'not_mod_actors' (listed classes that do not derive from ArgonSDKModBase), 'unmatched_excludes'.
    """
    issues = []
    name = lambda m: '/'.join(m.split('/')[-3:-1]) or m  # noqa: E731
    for m in sorted(info):
        data = info[m]
        if not data['id']:
            issues.append('{0}: ModId is empty'.format(name(m)))
        elif not is_pak_safe(data['id']):
            issues.append('{0}: ModId "{1}" is not pak-safe (letters, digits, . _ - only)'.format(name(m), data['id']))
        json_name = (json_names or {}).get(m)
        if data['id'] and json_name and is_pak_safe(json_name) and json_name != data['id']:  # display names can never match
            issues.append('{0}: ModId "{1}" differs from the mod.json name "{2}" (the registry uses the mod.json name)'.format(
                name(m), data['id'], json_name))
    all_ids = dict((m, d['id']) for m, d in info.items())
    all_ids.update(dict((m, i) for m, i in (other_ids or {}).items() if m not in info))
    for mod_id, markers in sorted(duplicate_ids(all_ids).items()):
        issues.append('ModId "{0}" is used by {1} markers: {2}'.format(mod_id, len(markers), ', '.join(name(m) for m in markers)))
    by_dir = {}
    for m, folder in tree['dir'].items():
        by_dir.setdefault(folder, []).append(m)
    for folder, markers in sorted(by_dir.items()):
        if len(markers) > 1:
            issues.append('{0} markers in the same folder {1}'.format(len(markers), folder))
    versions = dict((m, d['version']) for m, d in info.items())
    for m in tree['roots']:
        if m in info and (rooted is None or m in rooted) and not effective_version(m, versions, tree):
            issues.append('{0}: root marker has no ModVersion (nested markers inherit it)'.format(name(m)))
    for m in sorted(info):
        data = info[m]
        if data['separate'] and tree['parent'].get(m) is None:
            issues.append('{0}: OwnPak on a root marker has no effect'.format(name(m)))
        for ref in sorted(data['refs']):
            if ref == m:
                issues.append('{0}: references itself'.format(name(m)))
            elif ref not in info:
                issues.append('{0}: referenced marker not found: {1}'.format(name(m), ref))
            elif info[ref]['separate'] and tree['parent'].get(ref) is not None:
                issues.append('{0}: references {1}, which has OwnPak (it will not be merged)'.format(name(m), name(ref)))
        for path, owner in sorted(nested_owned_classes(m, data['classes'], tree).items()):
            issues.append('{0}: class {1} belongs to nested marker {2} - list it there (Refresh from folder removes it here)'.format(
                name(m), path.split('/')[-1], name(owner)))
        for path in sorted(data['classes']):
            if not exists(path):
                issues.append('{0}: class no longer exists: {1}'.format(name(m), path))
        for path in sorted(data.get('not_mod_actors') or []):
            issues.append('{0}: {1} is listed in ModActorClasses but is not an ArgonSDKModBase mod actor'.format(name(m), path.split('/')[-1]))
        convention = data.get('convention')
        if convention and convention not in data['classes'] and class_owner(convention.split('.')[0], dict(
                (x, tree['dir'][x]) for x in tree['dir'])) == m:
            issues.append('{0}: the folder-named mod actor {1} is not in ModActorClasses (V1 loaded it by naming convention) - run Refresh'.format(
                name(m), convention.split('/')[-1]))
        for prefix in sorted(data.get('unmatched_excludes') or []):
            issues.append('{0}: ExcludedPaths entry "{1}" matches nothing in the folder'.format(name(m), prefix))
    for cycle in reference_cycles(dict((m, [r for r in d['refs']]) for m, d in info.items())):
        issues.append('reference cycle: {0}'.format(' -> '.join(name(c) for c in cycle)))
    return issues


# -- editor side ---------------------------------------------------------------

def _mapmarker():
    import mapmarker
    return mapmarker


def _registry():
    return _mapmarker()._registry()


def _under(package, folder):
    return folder is None or package == folder or package.startswith(folder.rstrip('/') + '/')


def find_markers(folder=None):
    """Package paths of the V2 mod markers below `folder` (everywhere when None). Registry only."""
    return sorted(str(a.package_name) for a in _registry().get_assets_by_class(V2_INSTANCE_CLASS)
                  if _under(str(a.package_name), folder))


def find_v1_markers(folder):
    """V1 DA_ModMarker packages directly in `folder` (not below it)."""
    return sorted(str(a.package_name) for a in _registry().get_assets_by_class(V1_INSTANCE_CLASS)
                  if marker_dir(str(a.package_name)) == folder.rstrip('/'))


def _object_path(path):
    """'/Game/X/BP_Y.BP_Y_C' -> '/Game/X/BP_Y.BP_Y' (registry/asset lookups use the asset, not its class)."""
    return path[:-2] if path.endswith('_C') else path


def _asset_exists(path):
    import unreal
    return unreal.EditorAssetLibrary.does_asset_exist(_object_path(path))


def _paths(asset, name):
    return _mapmarker().read_paths(asset, name)


def read_marker(package):
    """The values of one marker as plain Python data (nothing referenced is loaded)."""
    import unreal
    asset = unreal.EditorAssetLibrary.load_asset(package)
    if asset is None:
        raise RuntimeError('marker not found: {0}'.format(package))
    get = _mapmarker()._get
    return {'id': str(get(asset, P_ID, '')), 'version': str(get(asset, P_VERSION, '')),
            'separate': bool(get(asset, P_SEPARATE, False)), 'refs': _paths(asset, P_REFS),
            'bundle_version': bool(get(asset, P_BUNDLE_VERSION, False)),
            'classes': _paths(asset, P_CLASSES),
            'exclude': [str(x) for x in get(asset, P_EXCLUDE, [])]}


def _v2_class(class_path=None):
    """The DA_ModMarker_V2 class that actually has the V2 fields (the ArgonSDK stub may still be empty)."""
    import unreal
    candidates = [class_path] if class_path else []
    candidates += ['/Game/Mods/ArgonSDK/Mods/ModLoading/v2/DataAssets/DA_ModMarker_V2.DA_ModMarker_V2_C']
    for a in _registry().get_assets_by_class('Blueprint'):
        if str(a.asset_name) == V2_CLASS_NAME:
            candidates.append('{0}.{1}_C'.format(a.package_name, V2_CLASS_NAME))
    for path in candidates:
        cls = unreal.load_object(None, path)
        if cls is None:
            continue
        try:
            unreal.get_default_object(cls).get_editor_property(P_ID)
            return cls
        except Exception:
            continue
    raise RuntimeError('no DA_ModMarker_V2 class with the V2 fields found')


def _parent_map():
    """{generated class path: parent class path} for every Blueprint in the project (registry tags only)."""
    parents = {}
    for a in _registry().get_assets_by_class('Blueprint'):
        gen, par = class_from_tag(a.get_tag_value('GeneratedClass')), class_from_tag(a.get_tag_value('ParentClass'))
        if gen and par:
            parents[gen] = par
    return parents


def owned_mod_classes(marker, all_markers=None, parents=None):
    """Mod actor classes (descendants of ArgonSDKModBase) that belong to this marker's own folder.

    Folders of nested markers belong to those markers, so their classes are left out.
    """
    folder = marker_dir(marker)
    others = [marker_dir(m) for m in (all_markers if all_markers is not None else find_markers(folder)) if m != marker]
    parents = parents if parents is not None else _parent_map()
    return sorted(c for c in parents
                  if owned_by(c.split('.')[0], folder, others) and descends_from(parents, c, MOD_BASE_CLASS))


def init_marker(marker, dry_run=False, save=True, parents=None):
    """Fill a marker from its folder: FormatVersion, ModId (only when empty), missing ModActorClasses.

    ModId comes from the mod.json `name` when that is pak-safe (a root marker: nearest mod.json up the tree; a
    nested one: its own folder's), otherwise from the folder name. Never removes or overwrites anything.
    parents: a precomputed _parent_map() to share between many calls. Returns
    {'added_classes': [...], 'id_set': bool, 'id_source': 'mod.json' | 'folder' | None}.
    """
    import unreal
    mm = _mapmarker()
    package, asset = mm._load_marker(marker)
    folder = marker_dir(package)
    result = {'added_classes': [], 'id_set': False, 'id_source': None}
    have = set(_paths(asset, P_CLASSES))
    classes = [c for c in owned_mod_classes(package, parents=parents) if c not in have]
    result['added_classes'] = classes
    new_id = None
    if not str(asset.get_editor_property(P_ID)):
        tree = build_tree(set(find_markers()) | set([package]))
        new_id, result['id_source'] = suggest_mod_id(folder, _read_mod_json_factory(), exact=tree['parent'].get(package) is not None)
        result['id_set'] = True
    if dry_run:
        return result
    if new_id:
        asset.set_editor_property(P_ID, new_id)
    if int(asset.get_editor_property(P_SCHEMA)) == 0:
        asset.set_editor_property(P_SCHEMA, SCHEMA_VERSION)
    if classes:
        value = asset.get_editor_property(P_CLASSES)
        for c in classes:
            value.add(unreal.SoftClassPath(c))
        asset.set_editor_property(P_CLASSES, value)
    if save:
        mm._save(asset)
    return result


def refresh_marker(marker, dry_run=False, save=True, parents=None):
    """Make a marker consistent with its folder: init_marker's additions, plus removing mod actor classes that
    live in the folder of a marker nested below it (they belong to that marker). Nothing else is removed.

    Also stamps ModVersion from mod.json (see plan_stamps). Returns {'added_classes', 'removed_classes', 'id_set', 'version_set'}.
    """
    import unreal
    mm = _mapmarker()
    package, asset = mm._load_marker(marker)
    result = init_marker(package, dry_run=dry_run, save=False, parents=parents)
    # the FULL marker tree (ancestors too): whether this marker is nested decides how its version is stamped
    tree = build_tree(set(find_markers()) | set([package]))
    result['removed_classes'] = sorted(nested_owned_classes(package, _paths(asset, P_CLASSES), tree))
    if not dry_run and result['removed_classes']:
        value = asset.get_editor_property(P_CLASSES)
        for c in result['removed_classes']:
            value.discard(unreal.SoftClassPath(c))
        asset.set_editor_property(P_CLASSES, value)
    current = str(asset.get_editor_property(P_VERSION))
    read_json = _read_mod_json_factory()
    wanted = plan_stamps(tree, {package: current}, lambda f, exact: mod_json_version(f, read_json, exact=exact)).get(package)
    result['version_set'] = wanted if wanted and wanted != current else None
    if not dry_run and result['version_set']:
        asset.set_editor_property(P_VERSION, result['version_set'])
    if not dry_run and save and (result['added_classes'] or result['removed_classes'] or result['id_set'] or result['version_set']):
        mm._save(asset)
    return result


def create_marker(folder, class_path=None, parents=None):
    """Create `<folder>/<FolderName>_ModMarker` (only if the folder has no V2 marker yet) and fill it. Returns its package."""
    import unreal
    folder = folder.rstrip('/')
    existing = [m for m in find_markers(folder) if marker_dir(m) == folder]
    if existing:
        return existing[0]
    # the registry can lag behind an asset created a moment ago (same editor tick): ask the asset itself too
    for known in (marker_name(folder), MARKER_NAME):
        if unreal.EditorAssetLibrary.does_asset_exist(folder + '/' + known):
            return folder + '/' + known
    factory = unreal.DataAssetFactory()
    factory.set_editor_property('DataAssetClass', _v2_class(class_path))
    asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(marker_name(folder), folder, unreal.DataAsset, factory)
    if asset is None:
        raise RuntimeError('could not create a marker in {0}'.format(folder))
    asset.set_editor_property(P_SCHEMA, SCHEMA_VERSION)
    _mapmarker()._save(asset)
    package = folder + '/' + marker_name(folder)
    init_marker(package, parents=parents)  # fills ModId (mod.json name or folder name) and the mod actor classes
    return package


def create_mod_marker_v2(name, path, mod_json=False):
    """V2 counterpart of modtools.create_mod_marker(name, path): returns (cdo, asset) the same way.

    The mod folder is `<path>/<name>`; the marker is `<path>/<name>/<name>_ModMarker`, filled in (ModId, version from a
    mod.json when there is one, the mod actor classes found in the folder) and saved. An existing marker is reused.
    mod_json=True also creates a missing mod.json first. Set further values on the returned `asset`, then save it
    (`unreal.EditorAssetLibrary.save_loaded_asset(asset)`).
    """
    import unreal
    folder = '{0}/{1}'.format(path.rstrip('/'), name)
    if mod_json:
        create_mod_json(folder)
    package = create_marker(folder)
    asset = unreal.EditorAssetLibrary.load_asset(package)
    return unreal.get_default_object(asset.get_class()), asset


def child_folders(folder):
    """Immediate sub-folders of `folder` that contain assets (registry only)."""
    prefix = folder.rstrip('/') + '/'
    names = set()
    for a in _registry().get_assets_by_path(folder.rstrip('/'), recursive=True):
        package = str(a.package_name)
        if package.startswith(prefix) and '/' in package[len(prefix):]:
            names.add(package[len(prefix):].split('/')[0])
    return sorted(n for n in names if not n.startswith('_'))


def scaffold_nested(root_folder, sub='Mods', dry_run=False, class_path=None):
    """Create a V2 marker in every mod folder directly under `<root>/<sub>` that has none (additive only)."""
    base = root_folder.rstrip('/') + '/' + sub
    have = set(marker_dir(m) for m in find_markers(base))
    todo = [base + '/' + n for n in child_folders(base) if base + '/' + n not in have]
    if not dry_run:
        parents = _parent_map() if todo else None
        for folder in todo:
            create_marker(folder, class_path, parents=parents)
    return {'created': todo, 'existing': sorted(have)}


def v1_contents(v1_packages, parents):
    """(mod actor class paths, dropped dependency packages) of V1 markers, read from the registry - nothing is loaded.

    A V1 marker hard-references its ModActors classes and its CustomObjects; the registry lists those dependencies,
    and Blueprint ancestry tells the mod actors (descendants of ArgonSDKModBase) from the rest.
    """
    registry = _registry()
    options = _mapmarker()._dependency_options()
    by_package = {}
    for c in parents:
        by_package.setdefault(c.split('.')[0], []).append(c)
    deps = []
    for package in v1_packages:
        # get_dependencies returns None for an asset the registry does not know yet (created this very tick)
        deps += [str(d) for d in (registry.get_dependencies(package, options) or [])
                 if str(d).startswith('/Game/') and str(d) != package]
    return classify_v1_dependencies(deps, by_package, parents, MOD_BASE_CLASS,
                                    ignore=set([V1_CLASS_PACKAGE, MOD_BASE_CLASS.split('.')[0]]))


def migrate_v1(folder, dry_run=False, save=True, class_path=None, parents=None):
    """Copy a folder's V1 DA_ModMarker into its V2 marker (created if missing). The V1 asset is left untouched.

    V1 ModActors keys -> ModActorClasses. V1 CustomObjects (assets a pak was meant to replace; never used by the
    loader) are NOT migrated - what a pak replaces is derived from its _Content folders - and are only counted in
    'dropped'. Reads the V1 marker's dependencies from the registry, so nothing it references is loaded.
    Returns {'v1_markers', 'classes' (added), 'dropped' (packages), 'marker'}.
    """
    import unreal
    mm = _mapmarker()
    v1_markers = find_v1_markers(folder)
    result = {'v1_markers': v1_markers, 'classes': [], 'dropped': [], 'marker': None}
    if not v1_markers:
        return result
    parents = parents if parents is not None else _parent_map()
    classes, result['dropped'] = v1_contents(v1_markers, parents)
    existing = [m for m in find_markers(folder) if marker_dir(m) == folder.rstrip('/')]
    have = set(_paths(unreal.EditorAssetLibrary.load_asset(existing[0]), P_CLASSES)) if existing else set()
    result['classes'] = [c for c in classes if c not in have]
    if dry_run:
        result['marker'] = existing[0] if existing else folder.rstrip('/') + '/' + marker_name(folder)
        return result
    marker = existing[0] if existing else create_marker(folder, class_path, parents=parents)
    asset = unreal.EditorAssetLibrary.load_asset(marker)
    have = set(_paths(asset, P_CLASSES))
    result['classes'] = [c for c in classes if c not in have]
    if result['classes']:
        value = asset.get_editor_property(P_CLASSES)
        for c in result['classes']:
            value.add(unreal.SoftClassPath(c))
        asset.set_editor_property(P_CLASSES, value)
        if save:
            mm._save(asset)
    result['marker'] = marker
    return result


def convention_class(folder, parents):
    """The mod actor class of the Blueprint named like `folder` (V1's loading convention), or None."""
    name = folder.rstrip('/').split('/')[-1]
    for a in _registry().get_assets_by_path(folder.rstrip('/'), recursive=False):
        if str(a.asset_name) == name and str(a.asset_class) == 'Blueprint':
            gen = class_from_tag(a.get_tag_value('GeneratedClass'))
            return gen if gen and descends_from(parents, gen, MOD_BASE_CLASS) else None
    return None


def unmatched_excludes(marker, prefixes):
    """ExcludedPaths prefixes (relative to the marker folder) that match no asset file on disk."""
    import os
    import unreal
    content = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
    base = os.path.join(content, marker_dir(marker)[len('/Game/'):].replace('/', os.sep))
    files = []
    for root, _dirs, names in os.walk(base):
        rel = os.path.relpath(root, base).replace(os.sep, '/')
        files += [(('' if rel == '.' else rel + '/') + os.path.splitext(n)[0]) for n in names if n.lower().endswith(('.uasset', '.umap'))]
    return [p for p in prefixes if p.strip() and not any(pattern_matches(f, p) for f in files)]


def collect(folder):
    """Every marker under `folder`, plus any marker they reference from elsewhere: {marker: info}."""
    info, queue = {}, find_markers(folder)
    parents = _parent_map()
    while queue:
        marker = queue.pop(0)
        if marker in info:
            continue
        info[marker] = read_marker(marker)
        info[marker]['convention'] = convention_class(marker_dir(marker), parents)
        info[marker]['not_mod_actors'] = [c for c in info[marker]['classes']
                                          if _asset_exists(c) and not descends_from(parents, c, MOD_BASE_CLASS)]
        info[marker]['unmatched_excludes'] = unmatched_excludes(marker, info[marker]['exclude'])
        for ref in info[marker]['refs']:
            ref_package = ref.split('.')[0]
            if ref_package not in info and _asset_exists(ref):
                queue.append(ref_package)
    return info


def _normalise_refs(info):
    for data in info.values():
        data['refs'] = [r.split('.')[0] for r in data['refs']]
    return info


def validate(folder):
    """Read-only audit of every marker under `folder`. Returns {'issues': [...], 'ok': bool, 'markers': n}."""
    info = _normalise_refs(collect(folder))
    tree = build_tree(info)
    rooted = set(m for m in info if _under(m, folder))
    read_json = _read_mod_json_factory()
    json_names = dict((m, mod_json_field(tree['dir'][m], read_json, 'name', exact=tree['parent'].get(m) is not None)) for m in info)
    others = dict((m, read_marker(m)['id']) for m in find_markers() if m not in info)
    issues = audit_markers(info, tree, _asset_exists, rooted, json_names, others)
    return {'issues': issues, 'ok': not issues, 'markers': len(info)}


def plan(folder):
    """The pak plan for the markers under `folder` (see pak_plan) plus the effective version of each marker."""
    info = _normalise_refs(collect(folder))
    tree = build_tree(info)
    ids = dict((m, d['id']) for m, d in info.items())
    versions = dict((m, d['version']) for m, d in info.items())
    result = pak_plan(tree, [m for m, d in info.items() if d['separate']],
                      dict((m, d['refs']) for m, d in info.items()), ids,
                      scope=[m for m in info if _under(m, folder)])
    result['versions'] = dict((m, effective_version(m, versions, tree)) for m in info)
    result['ids'] = ids
    result['dirs'] = dict(tree['dir'])
    result['excluded'] = dict((m, list(d['exclude'])) for m, d in info.items())
    result['replaces'] = dict((pak['stem'], sorted(set(t for m in pak['markers'] for t in replaced_assets(m))))
                              for pak in result['paks'])
    return result


def _read_mod_json_factory():
    """read_json(folder) for /Game/... folders, reading mod.json from the project's Content directory."""
    import json
    import os
    import unreal
    content = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())

    def read_json(folder):
        path = os.path.join(content, folder[len('/Game/'):].replace('/', os.sep), 'mod.json')
        try:
            with open(path) as handle:
                return json.load(handle)
        except (IOError, ValueError):
            return None
    return read_json


def _actor_cdo_property(cdo):
    for name in ACTOR_VERSION_PROPERTIES:
        try:
            cdo.get_editor_property(name)
            return name
        except Exception:
            continue
    return None


def stamp_actor_versions(wanted, dry_run=False, save=True):
    """Set ModVersion on the default object of each mod actor class in {class path: version}; only changes are
    written (and their Blueprint saved). Loads each class to read its current value. Returns {class path: version}
    of the classes that changed; classes that do not exist or have no ModVersion property are skipped."""
    import unreal
    mm = _mapmarker()
    changed = {}
    for class_path, version in sorted(wanted.items()):
        cls = unreal.load_object(None, class_path)
        if cls is None:
            continue
        cdo = unreal.get_default_object(cls)
        name = _actor_cdo_property(cdo)
        if name is None or str(cdo.get_editor_property(name)) == version:
            continue
        changed[class_path] = version
        if not dry_run:
            cdo.set_editor_property(name, version)
            asset = unreal.EditorAssetLibrary.load_asset(_object_path(class_path))
            if asset is not None and save:
                mm._save(asset)
    return changed


def stamp_all(folder, dry_run=False, save=True, actors=True):
    """Stamp versions for the cook: marker ModVersion from mod.json (plan_marker_versions, incl. ApplyVersionToBundled),
    then ModVersion on every mod actor class those markers list (plan_actor_versions).

    Returns {'markers': {marker: version}, 'actors': {class path: version}} - only what changed. Markers bundled from
    other directories are touched only when a flagged top marker claims them.
    """
    import unreal
    mm = _mapmarker()
    info = _normalise_refs(collect(folder))
    tree = build_tree(list(info))
    scope = [m for m in info if _under(m, folder)]
    versions = dict((m, d['version']) for m, d in info.items())
    paks = pak_plan(tree, [m for m, d in info.items() if d['separate']], dict((m, d['refs']) for m, d in info.items()),
                    dict((m, d['id']) for m, d in info.items()), scope=scope)['paks']
    read_json = _read_mod_json_factory()
    changes = plan_marker_versions(tree, versions, lambda f, exact: mod_json_version(f, read_json, exact=exact),
                                   paks, dict((m, d['bundle_version']) for m, d in info.items()), scope)
    if not dry_run:
        for marker, version in changes.items():
            asset = unreal.EditorAssetLibrary.load_asset(marker)
            asset.set_editor_property(P_VERSION, version)
            if save:
                mm._save(asset)
    result = {'markers': changes, 'actors': {}}
    if actors:
        after = dict(versions)
        after.update(changes)
        packed = set(m for pak in paks for m in pak['markers'])
        wanted = plan_actor_versions(tree, after, dict((m, d['classes']) for m, d in info.items()),
                                     [m for m in info if m in packed or m in scope])
        result['actors'] = stamp_actor_versions(wanted, dry_run=dry_run, save=save)
    return result


def stamp_versions(folder, dry_run=False, save=True, actors=True):
    """stamp_all() reduced to the marker part: {marker: new_version} (actors are stamped too unless actors=False)."""
    return stamp_all(folder, dry_run=dry_run, save=save, actors=actors)['markers']


# -- ModPackerWidget API (plain dicts, registry only unless noted) ------------------

def _own_marker(folder):
    """The V2 marker directly in `folder`, or None. Asks the asset too: the registry can lag behind a fresh asset."""
    import unreal
    folder = folder.rstrip('/')
    found = [m for m in find_markers(folder) if marker_dir(m) == folder]
    if found:
        return found[0]
    for known in (marker_name(folder), MARKER_NAME):
        if unreal.EditorAssetLibrary.does_asset_exist(folder + '/' + known):
            return folder + '/' + known
    return None


def status(mod_folder, with_issues=False):
    """Marker status of one mod folder, for the ModPackerWidget.

    {'state': 'v2' | 'v1_only' | 'none', 'marker': package or None, 'v1_markers': [...], 'issues': n or None,
     'mod_json': bool}  # mod_json: the folder has its own mod.json
    'v1_only' is the "needs a V2" case. with_issues=True also runs validate() (slower).
    """
    folder = mod_folder.rstrip('/')
    marker = _own_marker(folder)
    v1 = find_v1_markers(folder)
    result = {'state': marker_state([marker] if marker else [], v1), 'marker': marker, 'v1_markers': v1, 'issues': None,
              'mod_json': has_mod_json(folder)}
    if with_issues and marker:
        result['issues'] = len(validate(folder)['issues'])
    return result


def status_all(root):
    """{mod_folder: status} for every folder directly below `root` (e.g. /Game/Mods/AgMods or <collection>/Mods)."""
    base = root.rstrip('/')
    return dict((base + '/' + name, status(base + '/' + name)) for name in child_folders(base))


def create_v2_for(mod_folder, class_path=None, parents=None, mod_json=False):
    """Create the V2 marker for a mod folder, populated automatically: from its V1 marker when there is one,
    otherwise from the folder's own mod actor classes. Adds only; the V1 asset is untouched.

    Returns {'marker', 'created', 'source': 'v1' | 'folder', 'classes': n, 'version', 'dropped_objects': n,
    'removed_classes', 'mod_json_created': bool}. `parents` shares one _parent_map() across a batch. mod_json=True
    also creates a mod.json (version 0.1.0) first when the folder has none, so ModId/ModVersion come from it.
    """
    folder = mod_folder.rstrip('/')
    mod_json_created = bool(mod_json) and create_mod_json(folder)['created']
    parents = parents if parents is not None else _parent_map()
    had = _own_marker(folder) is not None
    dropped = 0
    if find_v1_markers(folder):
        report = migrate_v1(folder, class_path=class_path, parents=parents)
        marker, source, dropped = report['marker'], 'v1', len(report['dropped'])
    else:
        marker, source = create_marker(folder, class_path, parents=parents), 'folder'
    refreshed = refresh_marker(marker, parents=parents)
    data = read_marker(marker)
    return {'marker': marker, 'created': not had, 'source': source, 'classes': len(data['classes']),
            'version': data['version'], 'removed_classes': refreshed['removed_classes'], 'dropped_objects': dropped,
            'mod_json_created': mod_json_created}


def convert_v1_to_v2(mod_folder, confirm=True, delete_v1=True, mod_json=False, class_path=None):
    """Convert a mod folder's V1 DA_ModMarker into a V2 marker, then delete the V1 marker once the V2 is verified.

    For the ModPackerWidget (call it with ExecutePythonScript). Steps:
      1. nothing to do unless the folder has V1 marker(s); a V2 that already exists is reused and filled in
      2. confirm=True asks Yes/No first (what will be created, what will be deleted); No changes nothing
      3. create_v2_for() builds/fills the V2 marker and saves it
      4. verify: the V2 asset loads, has the V2 class, and holds every mod actor class the V1 marker listed
      5. only then delete the V1 marker(s) - and only when delete_v1 and nothing else references them; otherwise they
         are kept and named in 'kept'
    Returns {'status': 'converted' | 'no_v1' | 'declined' | 'verify_failed' | 'error', 'marker', 'classes',
    'deleted': [v1 packages], 'kept': [v1 packages], 'dropped_objects', 'message'}. Never raises.
    CustomObjects of the V1 marker are not migrated (a pak's replaced assets come from its _Content folders).
    """
    import unreal
    folder = mod_folder.rstrip('/')
    result = {'status': 'error', 'marker': None, 'classes': 0, 'deleted': [], 'kept': [], 'dropped_objects': 0, 'message': ''}
    try:
        v1 = find_v1_markers(folder)
        if not v1:
            result.update(status='no_v1', marker=_own_marker(folder), message='{0} has no V1 marker'.format(folder.split('/')[-1]))
            return result
        parents = _parent_map()
        expected, dropped = v1_contents(v1, parents)
        had_v2 = _own_marker(folder) is not None
        if confirm:
            import mapmarker_menu
            text = (u'Convert the V1 marker of "{0}" to a V2 marker?\n\n'
                    u'{1} mod actor class(es) are copied into {2}.\n'
                    u'{3} V1 CustomObjects are not migrated (a pak\'s replaced assets come from its _Content folders).\n\n'
                    u'{4}').format(folder.split('/')[-1], len(expected), u'the existing V2 marker' if had_v2 else u'a new V2 marker',
                                   len(dropped),
                                   u'After the V2 marker is verified, the V1 marker is DELETED.' if delete_v1 else u'The V1 marker is kept.')
            answer = mapmarker_menu._message_box('Convert V1 mod marker', text, yes_no=True)
            if answer is not True:     # No, or no dialog could be shown: never convert unasked
                result.update(status='declined', message='not confirmed')
                return result
        report = create_v2_for(folder, class_path=class_path, parents=parents, mod_json=mod_json)
        result.update(marker=report['marker'], classes=report['classes'], dropped_objects=report['dropped_objects'])
        # verify the V2 marker before touching the V1 one
        asset = unreal.EditorAssetLibrary.load_asset(report['marker']) if report['marker'] else None
        have = set(_paths(asset, P_CLASSES)) if asset is not None else set()
        missing = [c for c in expected if c not in have]
        if asset is None or asset.get_class().get_name() != V2_INSTANCE_CLASS or missing:
            result.update(status='verify_failed', kept=list(v1),
                          message='V2 marker did not verify (missing classes: {0}); V1 kept'.format(missing or 'none, asset/class wrong'))
            return result
        if delete_v1:
            registry = _registry()
            for package in v1:
                others = [str(r) for r in (registry.get_referencers(package, _mapmarker()._dependency_options()) or [])
                          if str(r) != package]
                if others:
                    result['kept'].append(package)
                elif unreal.EditorAssetLibrary.delete_asset(package):
                    result['deleted'].append(package)
                else:
                    result['kept'].append(package)
        else:
            result['kept'] = list(v1)
        result.update(status='converted', message='converted {0}: {1} class(es); V1 deleted: {2}, kept: {3}'.format(
            folder.split('/')[-1], result['classes'], len(result['deleted']), len(result['kept'])))
        return result
    except Exception as error:  # noqa: BLE001 - the widget gets a status, not a traceback
        result.update(status='error', message=str(error))
        return result


def migrate_v1_tree(folder, dry_run=False, class_path=None):
    """create_v2_for() every folder at or below `folder` that has a V1 marker but no V2 marker yet.

    Returns {'migrated': [folders], 'skipped': [folders that already have a V2]}.
    """
    registry = _registry()
    folders = sorted(set(marker_dir(str(a.package_name)) for a in registry.get_assets_by_class(V1_INSTANCE_CLASS)
                         if _under(str(a.package_name), folder)))
    todo = [f for f in folders if _own_marker(f) is None]
    if not dry_run and todo:
        parents = _parent_map()
        for f in todo:
            create_v2_for(f, class_path, parents=parents)
    return {'migrated': todo, 'skipped': [f for f in folders if f not in todo]}


def replaced_assets(marker):
    """Base-game assets a marker's pak replaces: everything under any _Content folder below its folder (its own, or one
    in a plain subfolder like Mods/ModLoading/_Content), as the /Game path it is mounted at. Derived from disk, so it can
    never drift (this replaces V1's unused CustomObjects)."""
    import os
    import unreal
    content = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
    base = os.path.join(content, marker_dir(marker)[len('/Game/'):].replace('/', os.sep))
    out = []
    for root, _dirs, files in os.walk(base):
        if '_Content' not in os.path.relpath(root, base).split(os.sep):
            continue
        for name in files:
            stem, ext = os.path.splitext(name)
            if ext.lower() in ('.uasset', '.umap'):
                relative = os.path.relpath(os.path.join(root, stem), content).replace(os.sep, '/')
                target = content_target('/Game/' + relative)
                if target:
                    out.append(target)
    return sorted(out)


def survey_v1(root):
    """Read-only look at every V1 marker below `root`: what a migration would copy and drop. Loads nothing.

    Returns [{'marker', 'folder', 'classes': n, 'dropped': n, 'has_v2': bool}], biggest dropped count first.
    """
    parents = _parent_map()
    rows = []
    for package in sorted(str(a.package_name) for a in _registry().get_assets_by_class(V1_INSTANCE_CLASS)
                          if _under(str(a.package_name), root)):
        classes, dropped = v1_contents([package], parents)
        rows.append({'marker': package, 'folder': marker_dir(package), 'classes': len(classes), 'dropped': len(dropped),
                     'has_v2': _own_marker(marker_dir(package)) is not None})
    return sorted(rows, key=lambda r: (-r['dropped'], r['marker']))


def _mod_json_path(folder):
    import os
    import unreal
    content = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())
    return os.path.join(content, folder.rstrip('/')[len('/Game/'):].replace('/', os.sep), 'mod.json')


def has_mod_json(folder):
    """True if `folder` itself has a mod.json (a nested mod usually relies on its collection's)."""
    import os
    return os.path.isfile(_mod_json_path(folder))


def create_mod_json(folder, version='0.1.0', authors=(), description='', overwrite=False):
    """Create `<folder>/mod.json` if the folder has none (never overwrites unless overwrite=True).

    `name` is the folder's marker ModId when it has one, otherwise the folder name; mods under /Game/Mods/AgMods get
    the ArgonSDK-Mods dependency and `ag_mod` like the existing ones; `version` starts at 0.1.0. Fill in
    repo_url, description, authors and tags by hand. Returns {'path', 'created', 'name'}.
    """
    import io
    import json
    folder = folder.rstrip('/')
    path = _mod_json_path(folder)
    marker = _own_marker(folder)
    name = (read_marker(marker)['id'] if marker else '') or folder.split('/')[-1]
    result = {'path': path, 'created': False, 'name': name}
    import os
    if os.path.isfile(path) and not overwrite:
        return result
    data = build_mod_json(name, version, ag_mod=folder.startswith('/Game/Mods/AgMods/'), authors=authors, description=description)
    with io.open(path, 'w', encoding='utf-8') as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + u'\n')
    result['created'] = True
    return result


def bump_version(folder, part='patch'):
    """Bump the version in the mod.json that applies to the marker in `folder`, then re-stamp the markers.

    A root marker bumps the nearest mod.json up the tree; a nested marker only its own folder's (it normally inherits
    the collection's version - bump the root instead). A mod.json without a version gets 0.1.0. Only the version value
    is edited. Returns {'path', 'old', 'new', 'stamped': {marker: version}}.
    """
    import io
    import json
    import os
    from collections import OrderedDict
    folder = folder.rstrip('/')
    marker = _own_marker(folder)
    tree = build_tree(set(find_markers()) | set([marker or folder + '/' + marker_name(folder)]))
    nested = tree['parent'].get(marker or folder + '/' + marker_name(folder)) is not None
    target = mod_json_folder(folder, lambda f: os.path.isfile(_mod_json_path(f)), exact=nested)
    if target is None:
        raise RuntimeError('{0} has no mod.json{1}'.format(
            folder.split('/')[-1], ' of its own (a nested mod inherits the collection\'s version - bump the root marker, or create one)' if nested else ''))
    path = _mod_json_path(target)
    with io.open(path, 'r', encoding='utf-8', newline='') as handle:
        text = handle.read()
    try:
        new_text, old, new = bump_version_text(text, part)
    except KeyError:  # no version field yet: add it (this one time the file is re-serialised)
        data = json.loads(text, object_pairs_hook=OrderedDict)
        old, new = '', '0.1.0'
        data['version'] = new
        new_text = json.dumps(data, indent=2, ensure_ascii=False) + u'\n'
    with io.open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(new_text)
    return {'path': path, 'old': old, 'new': new, 'stamped': stamp_versions(folder if not nested else folder)}
