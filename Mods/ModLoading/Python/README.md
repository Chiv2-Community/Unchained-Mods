# Map marker scripts (DA_MapMarker_V2)

Editor-only Python that lives next to the marker so it travels with this folder.

- `mapmarker.py` - logic. `find_base_maps` / `pending` / `layers_closure` / `audit_findings` are pure (unit
  tested); `populate_shipped`, `ignore_unused`, `update_layers`, `required_maps`, `validate` and
  `clear_maps` act on a marker. Everything goes through the Asset Registry, so **no map is ever loaded**.
- `mapmarker_menu.py` - the same actions in the Content Browser right-click (**Map marker ->**) and as
  toolbar buttons in the marker's asset editor (shown only when the marker is the selected asset as the
  editor opens; the right-click menu always works).

| Action | What it does |
|---|---|
| Populate shipped maps | adds the folder's standalone base maps (not sublevel layers) to `ShippedMapPaths`; skips shipped, ignored, extra and excluded maps; never removes |
| Preview populate | shows what Populate would add, changes nothing |
| Ignore unused maps | puts base maps that are not shipped into `IgnoredMaps` so Populate leaves them out |
| Update map layers | recomputes `ShippedMapLayers` (derived) = sublevels of the shipped + extra maps, transitively, project-wide |
| Validate marker | read-only audit: stale / conflicting entries, shipped maps that are layers of other shipped maps, maps outside the folder, out-of-date layers, undecided maps |
| List required maps | lists the shipped, extra and layer maps a cook needs (the Content Browser cannot select maps from 4.25 Python: the engine refuses Map assets) |
| Clear maps | empties shipped / ignored / layers after confirming (keeps extras, excludes, notes) |

Cooking: the ArgonSDKTooling cooker calls `mapmarker.cook_plan(mod_folder)` (editor only) and cooks only the
maps a marker needs - shipped, extra and layers - plus every non-map asset. `find_markers(folder)` returns the markers
below a folder; with several, their plans are merged. Layers outside the mod folder are reported, not cooked.

Popups: **Validate, List required maps and Clear always open a message box.** The other actions only log
(plus a short on-screen note) unless the **Popups** toggle in the marker editor's toolbar is ticked; it is
remembered per project in `Saved/mapmarker_ui.json`.

Marker properties: `ShippedMapPaths` (map path -> loading screen), `IgnoredMaps`, `ExtraRequiredMaps` and
`ShippedMapLayers` (path sets; the last one is derived), `ExcludeFolders` (empty = `old`, `backup`,
`reference`), `ExcludeNamePatterns` (globs on the map name, always additive, e.g. `*_bak*`), `Notes`.
All paths are `FSoftObjectPath` structs: a typed `TSoftObjectPtr<UWorld>` cannot be written from Python
without loading the map. (The Details panel therefore shows map rows expanded as Element -> Key -> value;
that is how Unreal draws struct keys.) A cook can call `mapmarker.required_maps(marker)` for
`{'shipped', 'extra', 'layers'}`.

Note: setting a property from Python does not mark the package dirty, so saving must force it
(`save_loaded_asset(asset, False)`); the helpers do.

## Where the classes live

`DA_ModMarker_V2` and `DA_MapMarker_V2` are in `Content/Mods/ArgonSDK/Mods/ModLoading/v2/DataAssets/` (the V1 class
`DA_ModMarker` stays one level up in `ModLoading`). The scripts find them there by path and fall back to a name search.
**Restart the editor after moving or recompiling either class** and before creating the next marker: 4.25's
Find-in-Blueprints keeps a class pointer cache keyed by class name, and creating an asset of a recompiled class in the
same session can crash the editor (`FBlueprintAssetHandler::FindHandler` from `AssetRegistry::AssetCreated`).

## Mod markers (`DA_ModMarker_V2`) - `modmarker.py`, `modmarker_menu.py`

The V2 mod marker names a mod and what it ships using soft paths only (nothing is loaded):
`ModId` (from the `mod.json` `name` when it is pak-safe, else the folder name; only filled when empty), `ModVersion` (from the
nearest `mod.json` `version`; empty = inherit the nearest ancestor's), `FormatVersion`, `Icon`, `ListTags`,
`ModActorClasses`, `BundledMarkers` (any directory; packaged with their own real paths - not a dependency, those live in
`mod.json`), `ExcludedPaths` (patterns relative to the marker folder: a prefix like `Old/`, or `*`/`?` wildcards like `*_bak`;
see ArgonSDK-Tooling), `OwnPak`. V1's `CustomObjects` (assets a pak replaces) was never used and is dropped: what a pak
replaces is derived from its `_Content` folders and shown in **Preview pak plan**.
Per-mod name/author/flags stay on the `ArgonSDKModBase` actors. Markers **nest by folder** - the parent is the nearest
marker above, no field needed - and by default a nested marker's content merges into the root marker's pak;
`OwnPak` gives a nested marker its own `<ModId>.pak`.

Marker actions (right-click a V2 marker, or its editor toolbar): **Refresh from folder** (ModId, FormatVersion,
ModVersion from `mod.json`, new mod actor classes; classes that belong to a nested marker are removed), **Validate**
(ids, versions, bundled markers, missing classes/assets, duplicate claims, cycles), **Preview pak plan**, **Bump version** (right-click: patch; toolbar also minor/major - rewrites only the top-level `version` of the folder's own `mod.json`, never a dependency's, then re-stamps the markers; a nested marker without its own `mod.json` refuses, it inherits the collection's), **Create mod.json** (when the folder has none: name from `ModId`, `version` 0.1.0, registry-shaped, never overwrites), **Scaffold nested
markers** (a marker in every folder under `<folder>/Mods` that has none) and **Migrate V1 markers below** (a populated
V2 for every folder below that only has a V1 marker). The last two ask first; V1 markers are never changed.
New markers are named `<FolderName>_ModMarker` (easy to tell apart in the asset browser and pickers; `modmarker.marker_name(folder)`); existing `ModMarker` assets are found by class and stay valid.
Right-click any other asset in a mod folder that has no V2 marker: **Create mod marker (this folder)** - populated from
the V1 marker when there is one (4.25 Python cannot reach the folder context menu, hence the asset menu).

`ModVersion`: a root marker takes the nearest `mod.json` `version` walking up; a nested marker only one in its own folder,
otherwise it inherits through the marker tree (so a root version change never rewrites nested markers).
`modmarker.stamp_all(folder)` (called by the cook preflight; `stamp_versions` is its marker-only view) stamps the markers and then `ModVersion` on every mod actor class they list, writing only what changed; the actors take their marker's effective version.
Optional bool `ApplyVersionToBundled` on `DA_ModMarker_V2` (add it to the class, category Packaging; markers without it behave as false): when set on a top marker, its version (its `mod.json`, else its current `ModVersion`) is forced onto every marker packed into its pak - nested ones merged into it and `BundledMarkers` from other directories - and onto their actors, whatever their own `mod.json` says. `OwnPak` markers are other paks and keep their own version. Note this edits the bundled mods' marker and actor assets (a later cook of a bundled mod on its own stamps its own version back).

**For the ModPackerWidget** (plain dicts, registry only, fast - 133 folders in ~0.04 s):
`modmarker.status(folder)` -> `{'state': 'v2' | 'v1_only' | 'none', 'marker', 'v1_markers', 'issues'}` (show an exclamation
mark for `v1_only`), `modmarker.status_all(root)` -> `{folder: status}` for the folders under a root, and
`modmarker.create_v2_for(folder, mod_json=False)` -> creates the V2 populated automatically (V1 first, else the folder; with
`mod_json=True` it also creates a missing mod.json first) and returns a report; `status` also tells whether the folder has its own `mod.json`.
`modmarker.convert_v1_to_v2(folder, confirm=True, delete_v1=True, mod_json=False)` is the widget's V1 -> V2 button: Yes/No prompt
(says the V1 marker will be deleted and that V1 CustomObjects are not migrated), builds the V2 marker, **verifies** it (loads,
V2 class, every V1 mod actor class present) and only then deletes the V1 marker - and only when nothing else references it
(otherwise it is listed under `kept`). Returns `{'status': 'converted' | 'no_v1' | 'declined' | 'verify_failed' | 'error',
'marker', 'classes', 'deleted', 'kept', 'dropped_objects', 'message'}` and never raises. Use `delete_v1=False` while the loader
(`sdk_tweaks-e1t.8`) still only reads V1. `modmarker.create_mod_marker_v2(name, path, mod_json=False)` is the V2 counterpart of
`modtools.create_mod_marker` and returns `(cdo, asset)` (marker `<path>/<name>/<name>_ModMarker`, reused if present);
`mapmarker.create_map_marker(folder, name='MapMarker', populate=True)` does the same for map markers (populate = the normal
Populate action).
Nothing else here moves, renames or deletes an existing asset. Other Python API: `find_markers / create_marker / refresh_marker /
scaffold_nested / migrate_v1 / migrate_v1_tree / validate / plan`; the pure core is unit-tested.

**Calling from a Blueprint** (Execute Python Script node): give the node typed pins - dict results do not fit one `Map` pin when
their values differ in type - e.g. input `ModFolder` (String); outputs `Status`, `Message`, `Marker` (String), `Classes` (Integer),
`Deleted`, `Kept` (Array of String), and assign them from the returned dict (`Marker = r['marker'] or ''`).

## Enable (project `Config/DefaultEngine.ini`, section `[/Script/PythonScriptPlugin.PythonScriptPluginSettings]`)

```ini
+AdditionalPaths=(Path="../../../TBL/Content/Mods/ArgonSDK/Mods/ModLoading/Python")
+StartupScripts=import sys, unreal; p = unreal.Paths.project_content_dir() + 'Mods/ArgonSDK/Mods/ModLoading/Python'; sys.path.append(p) if p not in sys.path else None; import mapmarker_menu; mapmarker_menu.register()
```

The `AdditionalPaths` path is relative to the engine's `Binaries/Win64`, so it only resolves for some
layouts; the startup script builds the path from the project instead and works everywhere. Restart the editor
after changing either.

## Tests

`python3 -m unittest discover -s tests` from this folder.
