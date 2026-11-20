import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import modmarker as mm  # noqa: E402

R = '/Game/Mods/ArgonSDK'
ROOT = R + '/ModMarker'
ALPHA = R + '/Mods/Alpha/ModMarker'
BETA = R + '/Mods/Beta/ModMarker'
DEEP = R + '/Mods/Alpha/Sub/ModMarker'
EXT = '/Game/Mods/AgMods/Ext/ModMarker'


class TreeTests(unittest.TestCase):
    def test_parent_is_nearest_marker_above(self):
        tree = mm.build_tree([ROOT, ALPHA, BETA, DEEP])
        self.assertEqual(tree['roots'], [ROOT])
        self.assertEqual(tree['parent'][ALPHA], ROOT)
        self.assertEqual(tree['parent'][DEEP], ALPHA)  # nearest, not the root
        self.assertEqual(sorted(tree['children'][ROOT]), [ALPHA, BETA])

    def test_unrelated_markers_are_separate_roots(self):
        tree = mm.build_tree([ROOT, EXT])
        self.assertEqual(tree['roots'], sorted([ROOT, EXT]))

    def test_markers_in_the_same_folder_are_peers(self):
        tree = mm.build_tree([R + '/A/ModMarker', R + '/A/Other'])
        self.assertEqual(len(tree['roots']), 2)

    def test_folder_name_prefix_is_not_ancestry(self):
        tree = mm.build_tree([R + '/Mods/Al/ModMarker', R + '/Mods/Alpha/ModMarker'])
        self.assertEqual(len(tree['roots']), 2)

    def test_subtree_order(self):
        tree = mm.build_tree([ROOT, ALPHA, BETA, DEEP])
        self.assertEqual(mm.subtree(tree, ROOT), [ROOT, ALPHA, DEEP, BETA])


class VersionTests(unittest.TestCase):
    def test_empty_version_inherits_the_nearest_ancestor(self):
        tree = mm.build_tree([ROOT, ALPHA, DEEP])
        v = {ROOT: '1.0', ALPHA: '', DEEP: ''}
        self.assertEqual(mm.effective_version(DEEP, v, tree), '1.0')
        v[ALPHA] = '2.0'
        self.assertEqual(mm.effective_version(DEEP, v, tree), '2.0')
        self.assertEqual(mm.effective_version(ROOT, {ROOT: ''}, tree), '')


class OwnershipTests(unittest.TestCase):
    def test_nested_marker_folders_belong_to_the_nested_marker(self):
        own, others = R, [R + '/Mods/Alpha']
        self.assertTrue(mm.owned_by(R + '/Common/X', own, others))
        self.assertFalse(mm.owned_by(R + '/Mods/Alpha/BP_A', own, others))
        self.assertFalse(mm.owned_by('/Game/Other/X', own, others))
        self.assertTrue(mm.owned_by(R + '/Mods/Alpha/BP_A', R + '/Mods/Alpha', others))


class IdTests(unittest.TestCase):
    def test_duplicates_ignore_empty_ids(self):
        out = mm.duplicate_ids({ROOT: 'a', ALPHA: 'a', BETA: '', EXT: ''})
        self.assertEqual(out, {'a': sorted([ALPHA, ROOT])})


class CycleTests(unittest.TestCase):
    def test_finds_a_cycle(self):
        cycles = mm.reference_cycles({'a': ['b'], 'b': ['c'], 'c': ['a']})
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0][0], cycles[0][-1])

    def test_no_cycle(self):
        self.assertEqual(mm.reference_cycles({'a': ['b'], 'b': []}), [])

    def test_self_reference_is_a_cycle(self):
        self.assertEqual(len(mm.reference_cycles({'a': ['a']})), 1)


class PakPlanTests(unittest.TestCase):
    def setUp(self):
        self.tree = mm.build_tree([ROOT, ALPHA, BETA, DEEP, EXT])
        self.ids = {ROOT: 'Coll', ALPHA: 'Alpha', BETA: 'Beta', DEEP: 'Deep', EXT: 'Ext'}

    def _paks(self, plan):
        return dict((p['stem'], p['markers']) for p in plan['paks'])

    def test_nested_markers_merge_into_the_root_pak(self):
        plan = mm.pak_plan(self.tree, [], {}, self.ids)
        paks = self._paks(plan)
        self.assertEqual(paks['Coll'], sorted([ROOT, ALPHA, BETA, DEEP]))
        self.assertEqual(paks['Ext'], [EXT])
        self.assertEqual(plan['warnings'], [])

    def test_separate_pak_leaves_the_parent_pak_with_its_subtree(self):
        plan = mm.pak_plan(self.tree, [ALPHA], {}, self.ids)
        paks = self._paks(plan)
        self.assertEqual(paks['Coll'], sorted([ROOT, BETA]))
        self.assertEqual(paks['Alpha'], sorted([ALPHA, DEEP]))
        self.assertTrue([p for p in plan['paks'] if p['stem'] == 'Alpha'][0]['separate'])

    def test_cross_directory_reference_is_merged_with_real_paths(self):
        paks = self._paks(mm.pak_plan(self.tree, [], {ROOT: [EXT]}, self.ids))
        self.assertIn(EXT, paks['Coll'])
        self.assertIn(EXT, paks['Ext'])  # still its own root pak too -> flagged below

    def test_marker_in_two_paks_warns(self):
        plan = mm.pak_plan(self.tree, [], {ROOT: [EXT]}, self.ids)
        self.assertTrue(any('more than one pak' in w for w in plan['warnings']))

    def test_referencing_a_separate_marker_does_not_merge_it(self):
        plan = mm.pak_plan(self.tree, [BETA], {ROOT: [BETA]}, self.ids)
        self.assertNotIn(BETA, self._paks(plan)['Coll'])
        self.assertTrue(any('OwnPak' in w for w in plan['warnings']))

    def test_separate_on_a_root_is_ignored(self):
        plan = mm.pak_plan(self.tree, [ROOT], {}, self.ids)
        self.assertFalse([p for p in plan['paks'] if p['root'] == ROOT][0]['separate'])

    def test_scope_keeps_outside_markers_as_merged_content_only(self):
        plan = mm.pak_plan(self.tree, [], {ROOT: [EXT]}, self.ids, scope=[ROOT, ALPHA, BETA, DEEP])
        paks = self._paks(plan)
        self.assertEqual(sorted(paks), ['Coll'])  # no pak of its own for the external marker
        self.assertIn(EXT, paks['Coll'])
        self.assertEqual(plan['warnings'], [])

    def test_reference_cycle_terminates(self):
        plan = mm.pak_plan(self.tree, [], {ROOT: [EXT], EXT: [ROOT]}, self.ids)
        self.assertEqual(len(plan['paks']), 2)

    def test_stem_falls_back_to_the_folder_name(self):
        plan = mm.pak_plan(mm.build_tree([ROOT]), [], {}, {ROOT: ''})
        self.assertEqual(plan['paks'][0]['stem'], 'ArgonSDK')


def _info(**kw):
    base = {'id': 'x', 'version': '', 'separate': False, 'refs': [], 'classes': []}
    base.update(kw)
    return base


class AuditTests(unittest.TestCase):
    def _audit(self, info, exists=lambda p: True):
        return mm.audit_markers(info, mm.build_tree(info), exists)

    def test_clean_collection(self):
        info = {ROOT: _info(id='Coll', version='1.0'), ALPHA: _info(id='Alpha')}
        self.assertEqual(self._audit(info), [])

    def test_reports_empty_and_duplicate_ids_and_missing_root_version(self):
        info = {ROOT: _info(id='a'), ALPHA: _info(id='a'), BETA: _info(id='')}
        text = '\n'.join(self._audit(info))
        self.assertIn('ModId is empty', text)
        self.assertIn('is used by 2 markers', text)
        self.assertIn('root marker has no ModVersion', text)

    def test_missing_targets_and_references(self):
        info = {ROOT: _info(id='c', version='1', classes=['/Game/Gone.Gone_C'], refs=[EXT, ROOT])}
        text = '\n'.join(self._audit(info, exists=lambda p: False))
        self.assertIn('class no longer exists', text)
        self.assertIn('referenced marker not found', text)
        self.assertIn('references itself', text)

    def test_reference_to_a_separate_marker_and_separate_root(self):
        info = {ROOT: _info(id='c', version='1', refs=[ALPHA], separate=True), ALPHA: _info(id='a', separate=True)}
        text = '\n'.join(self._audit(info))
        self.assertIn('which has OwnPak', text)
        self.assertIn('OwnPak on a root marker has no effect', text)

    def test_cycle_is_reported(self):
        info = {ROOT: _info(id='c', version='1', refs=[EXT]), EXT: _info(id='e', version='1', refs=[ROOT])}
        self.assertIn('reference cycle', '\n'.join(self._audit(info)))

    def test_two_markers_in_one_folder(self):
        info = {R + '/A/ModMarker': _info(id='a', version='1'), R + '/A/Second': _info(id='b', version='1')}
        self.assertIn('markers in the same folder', '\n'.join(self._audit(info)))


class ClassTests(unittest.TestCase):
    BASE = '/Game/Base.Base_C'

    def test_descends_through_the_chain(self):
        parents = {'/G/A.A_C': self.BASE, '/G/B.B_C': '/G/A.A_C', '/G/X.X_C': '/Script/Engine.Actor'}
        self.assertTrue(mm.descends_from(parents, '/G/B.B_C', self.BASE))
        self.assertFalse(mm.descends_from(parents, '/G/X.X_C', self.BASE))
        self.assertFalse(mm.descends_from(parents, self.BASE, self.BASE))  # the base itself is not a mod

    def test_parent_cycle_terminates(self):
        self.assertFalse(mm.descends_from({'/a': '/b', '/b': '/a'}, '/a', self.BASE))

    def test_class_from_tag(self):
        self.assertEqual(mm.class_from_tag("BlueprintGeneratedClass'/Game/X.X_C'"), '/Game/X.X_C')
        self.assertEqual(mm.class_from_tag(''), '')
        self.assertEqual(mm.class_from_tag(None), '')


class NestedOwnershipTests(unittest.TestCase):
    def test_class_owner_is_the_closest_marker_folder(self):
        dirs = {ROOT: R, ALPHA: R + '/Mods/Alpha'}
        self.assertEqual(mm.class_owner(R + '/Mods/Alpha/BP_A', dirs), ALPHA)
        self.assertEqual(mm.class_owner(R + '/Common/BP_C', dirs), ROOT)
        self.assertIsNone(mm.class_owner('/Game/Other/BP_X', dirs))

    def test_only_classes_in_nested_marker_folders_are_flagged(self):
        tree = mm.build_tree([ROOT, ALPHA, BETA])
        classes = [R + '/Mods/Alpha/BP_A.BP_A_C', R + '/Common/BP_C.BP_C_C', R + '/Mods/Beta/Deep/BP_B.BP_B_C']
        flagged = mm.nested_owned_classes(ROOT, classes, tree)
        self.assertEqual(flagged, {classes[0]: ALPHA, classes[2]: BETA})
        self.assertEqual(mm.nested_owned_classes(ALPHA, classes, tree), {})  # a leaf owns what it lists

    def test_audit_reports_a_duplicated_claim(self):
        info = {ROOT: _info(id='c', version='1', classes=[R + '/Mods/Alpha/BP_A.BP_A_C']), ALPHA: _info(id='a')}
        issues = mm.audit_markers(info, mm.build_tree(info), lambda p: True)
        self.assertTrue(any('belongs to nested marker' in i for i in issues))


class StampTests(unittest.TestCase):
    JSON = {R: {'version': '1.0'}, R + '/Mods/Beta': {'version': '2.0'}, R + '/Mods/Gamma': {'name': 'no version'},
            '/Game/Mods/AgMods/Ext': {'version': ' 0.1.0 '}}

    def _read(self, folder):
        return self.JSON.get(folder)

    def test_exact_vs_walking_up(self):
        self.assertEqual(mm.mod_json_version(R + '/Mods/Alpha', self._read), '1.0')      # nearest ancestor
        self.assertIsNone(mm.mod_json_version(R + '/Mods/Alpha', self._read, exact=True))
        self.assertEqual(mm.mod_json_version(R + '/Mods/Beta', self._read, exact=True), '2.0')

    def test_mod_json_without_a_version_is_skipped_when_walking_up(self):
        self.assertEqual(mm.mod_json_version(R + '/Mods/Gamma', self._read), '1.0')
        self.assertIsNone(mm.mod_json_version(R + '/Mods/Gamma', self._read, exact=True))

    def test_version_is_trimmed_and_never_searches_above_game(self):
        self.assertEqual(mm.mod_json_version('/Game/Mods/AgMods/Ext', self._read, exact=True), '0.1.0')
        self.assertIsNone(mm.mod_json_version('/Game/Nothing', self._read))

    def test_plan_stamps_roots_walk_up_nested_use_their_own_only(self):
        tree = mm.build_tree([ROOT, ALPHA, BETA, EXT])
        lookup = lambda f, exact: mm.mod_json_version(f, self._read, exact=exact)  # noqa: E731
        changes = mm.plan_stamps(tree, {ROOT: '', ALPHA: '', BETA: '', EXT: ''}, lookup)
        self.assertEqual(changes, {ROOT: '1.0', BETA: '2.0', EXT: '0.1.0'})  # Alpha keeps inheriting

    def test_plan_stamps_skips_markers_already_correct(self):
        tree = mm.build_tree([ROOT, BETA])
        lookup = lambda f, exact: mm.mod_json_version(f, self._read, exact=exact)  # noqa: E731
        self.assertEqual(mm.plan_stamps(tree, {ROOT: '1.0', BETA: '9.9'}, lookup), {BETA: '2.0'})


class StateTests(unittest.TestCase):
    def test_marker_state(self):
        self.assertEqual(mm.marker_state([ROOT], []), 'v2')
        self.assertEqual(mm.marker_state([ROOT], ['/v1']), 'v2')  # a V2 wins over a leftover V1
        self.assertEqual(mm.marker_state([], ['/v1']), 'v1_only')
        self.assertEqual(mm.marker_state([], []), 'none')


class ModIdTests(unittest.TestCase):
    def test_pak_safe_names(self):
        for ok in ('Chiv2Turbo', 'Unchained-Mods', 'My_Mod.2', 'a'):
            self.assertTrue(mm.is_pak_safe(ok), ok)
        for bad in ('', 'ArgonSDK: Example Mod', 'has space', '-lead', '.hidden', 'a/b', None):
            self.assertFalse(mm.is_pak_safe(bad), repr(bad))

    def test_id_comes_from_the_nearest_mod_json_for_roots(self):
        read = {R: {'name': 'Unchained-Mods'}}.get
        self.assertEqual(mm.suggest_mod_id(R, read), ('Unchained-Mods', 'mod.json'))
        self.assertEqual(mm.suggest_mod_id(R + '/Deep/Folder', read), ('Unchained-Mods', 'mod.json'))

    def test_nested_markers_only_use_their_own_mod_json(self):
        read = {R: {'name': 'Unchained-Mods'}}.get
        self.assertEqual(mm.suggest_mod_id(R + '/Mods/Alpha', read, exact=True), ('Alpha', 'folder'))
        read = {R + '/Mods/Alpha': {'name': 'Alpha-Mod'}}.get
        self.assertEqual(mm.suggest_mod_id(R + '/Mods/Alpha', read, exact=True), ('Alpha-Mod', 'mod.json'))

    def test_display_name_falls_back_to_the_folder_name(self):
        read = {'/Game/Mods/AgMods/ExampleMod': {'name': 'ArgonSDK: Example Mod'}}.get
        self.assertEqual(mm.suggest_mod_id('/Game/Mods/AgMods/ExampleMod', read), ('ExampleMod', 'folder'))

    def test_no_mod_json_uses_the_folder_name(self):
        self.assertEqual(mm.suggest_mod_id(R + '/Mods/Alpha', lambda f: None), ('Alpha', 'folder'))

    def test_a_display_name_in_mod_json_is_not_a_mismatch(self):
        info = {ROOT: _info(id='ArgonSDK', version='1')}
        issues = mm.audit_markers(info, mm.build_tree(info), lambda p: True, json_names={ROOT: 'ArgonSDK: Example Mod'})
        self.assertEqual(issues, [])

    def test_audit_flags_unsafe_ids_and_mod_json_mismatch(self):
        info = {ROOT: _info(id='Bad Id', version='1'), ALPHA: _info(id='Alpha')}
        tree = mm.build_tree(info)
        text = '\n'.join(mm.audit_markers(info, tree, lambda p: True, json_names={ROOT: 'Good-Name', ALPHA: 'Alpha'}))
        self.assertIn('not pak-safe', text)
        self.assertIn('differs from the mod.json name "Good-Name"', text)
        self.assertNotIn('Alpha: ModId "Alpha" differs', text)


class V1MigrationTests(unittest.TestCase):
    BASE = '/Game/Mods/ArgonSDK/Mods/ArgonSDKModBase.ArgonSDKModBase_C'
    PARENTS = {'/Game/M/BP_A.BP_A_C': BASE, '/Game/M/BP_B.BP_B_C': '/Game/M/BP_A.BP_A_C',
               '/Game/M/BP_Plain.BP_Plain_C': '/Script/Engine.Actor'}
    BY_PKG = {'/Game/M/BP_A': ['/Game/M/BP_A.BP_A_C'], '/Game/M/BP_B': ['/Game/M/BP_B.BP_B_C'],
              '/Game/M/BP_Plain': ['/Game/M/BP_Plain.BP_Plain_C']}

    def test_mod_actors_are_kept_everything_else_is_dropped(self):
        deps = ['/Game/M/BP_A', '/Game/M/BP_B', '/Game/M/BP_Plain', '/Game/M/SomeMesh', '/Game/Mods/ArgonSDK/Mods/ArgonSDKModBase']
        classes, dropped = mm.classify_v1_dependencies(deps, self.BY_PKG, self.PARENTS, self.BASE,
                                                       ignore=set(['/Game/Mods/ArgonSDK/Mods/ArgonSDKModBase']))
        self.assertEqual(classes, ['/Game/M/BP_A.BP_A_C', '/Game/M/BP_B.BP_B_C'])
        self.assertEqual(sorted(dropped), ['/Game/M/BP_Plain', '/Game/M/SomeMesh'])

    def test_duplicates_collapse_and_empty_is_fine(self):
        classes, dropped = mm.classify_v1_dependencies(['/Game/M/BP_A', '/Game/M/BP_A'], self.BY_PKG, self.PARENTS, self.BASE)
        self.assertEqual((classes, dropped), (['/Game/M/BP_A.BP_A_C'], []))
        self.assertEqual(mm.classify_v1_dependencies([], {}, {}, self.BASE), ([], []))


class ReplacedAssetsTests(unittest.TestCase):
    def test_content_target_maps_to_the_base_game_path(self):
        self.assertEqual(mm.content_target(R + '/Mods/ModLoading/_Content/UI/Menu/Widget'), '/Game/UI/Menu/Widget')
        self.assertIsNone(mm.content_target(R + '/Mods/ModLoading/Widget'))


class ExtraAuditTests(unittest.TestCase):
    def _audit(self, info, **kw):
        return '\n'.join(mm.audit_markers(info, mm.build_tree(info), lambda p: True, **kw))

    def test_folder_named_mod_actor_must_be_listed(self):
        conv = R + '/Mods/Alpha/Alpha.Alpha_C'
        info = {ALPHA: _info(id='Alpha', version='1', convention=conv)}
        self.assertIn('folder-named mod actor Alpha.Alpha_C is not in ModActorClasses', self._audit(info))
        info[ALPHA]['classes'] = [conv]
        self.assertNotIn('folder-named', self._audit(info))

    def test_convention_class_owned_by_a_nested_marker_is_not_blamed_on_the_parent(self):
        conv = R + '/Mods/Alpha/Alpha.Alpha_C'
        info = {ROOT: _info(id='c', version='1', convention=conv), ALPHA: _info(id='a', classes=[conv])}
        self.assertNotIn('folder-named', self._audit(info))

    def test_non_mod_actors_and_unmatched_excludes(self):
        info = {ROOT: _info(id='c', version='1', classes=['/Game/X/Plain.Plain_C'], not_mod_actors=['/Game/X/Plain.Plain_C'],
                            unmatched_excludes=['Old/'])}
        text = self._audit(info)
        self.assertIn('Plain.Plain_C is listed in ModActorClasses but is not an ArgonSDKModBase mod actor', text)
        self.assertIn('ExcludedPaths entry "Old/" matches nothing', text)

    def test_modid_must_be_unique_across_the_project(self):
        info = {ROOT: _info(id='Same', version='1')}
        self.assertIn('is used by 2 markers', self._audit(info, other_ids={EXT: 'Same'}))
        self.assertNotIn('is used by', self._audit(info, other_ids={EXT: 'Different'}))


VECTORS = [
    ('Old/Thing', 'Old/', True), ('Older/Thing', 'Old', True), ('Old', 'Old/', False), ('Newer/Old', 'Old', False),
    ('Maps/Town_bak', '*_bak', True), ('Maps/Town_bak/Deep/X', '*_bak', True), ('Maps/Town', '*_bak', False),
    ('X/Backup/A', '*/Backup', True), ('Backup/A', '*/Backup', False),
    ('Maps/Town_old', 'Maps/*_old', True), ('maps/town_OLD', 'MAPS/*_old', True), ('Other/Town_old', 'Maps/*_old', False),
    ('a1', 'a?', True), ('a12', 'a?', False), ('Maps/Town.v2', '*.v2', True), ('Maps/TownXv2', '*.v2', False),
    ('Foo', '', False), ('Foo', '   ', False), ('Foo/Bar', '/Foo/', True), ('Foo/Bar', 'Foo\\', True),
]


class ExcludePatternTests(unittest.TestCase):
    def test_shared_vectors_with_the_cook(self):
        for rel, pattern, expected in VECTORS:
            self.assertEqual(mm.pattern_matches(rel, pattern), expected, '{0!r} vs {1!r}'.format(rel, pattern))


class ModJsonTests(unittest.TestCase):
    def test_shape_and_key_order_match_the_existing_files(self):
        import json
        data = mm.build_mod_json('Foo', authors=['me'], description='d', repo_url='https://x', tags=['Mutator'])
        self.assertEqual(list(data), ['repo_url', 'name', 'description', 'mod_type', 'authors', 'dependencies', 'tags', 'version'])
        self.assertEqual((data['name'], data['version'], data['mod_type'], data['dependencies']), ('Foo', '0.1.0', 'Shared', []))
        self.assertEqual(json.loads(json.dumps(data))['authors'], ['me'])

    def test_agmods_get_the_flag_and_the_argonsdk_dependency(self):
        data = mm.build_mod_json('Foo', ag_mod=True)
        self.assertTrue(data['ag_mod'])
        self.assertEqual(data['dependencies'], [{'repo_url': mm.ARGON_MODS_REPO, 'version': '>=0.0.1'}])
        self.assertEqual(list(data), ['repo_url', 'name', 'description', 'mod_type', 'authors', 'dependencies', 'ag_mod', 'tags', 'version'])

    def test_custom_version(self):
        self.assertEqual(mm.build_mod_json('Foo', version='2.1.0')['version'], '2.1.0')


class BumpTests(unittest.TestCase):
    def test_bump_parts(self):
        self.assertEqual(mm.bump_semver('1.2.3'), '1.2.4')
        self.assertEqual(mm.bump_semver('1.2.3', 'minor'), '1.3.0')
        self.assertEqual(mm.bump_semver('1.2.3', 'major'), '2.0.0')
        self.assertEqual(mm.bump_semver(' 0.9.9 ', 'patch'), '0.9.10')

    def test_prerelease_and_build_suffixes_are_dropped(self):
        self.assertEqual(mm.bump_semver('1.0.0-beta.1'), '1.0.1')
        self.assertEqual(mm.bump_semver('1.0.0+build5', 'minor'), '1.1.0')

    def test_non_semver_and_unknown_part_are_rejected(self):
        for bad in ('1.0', 'v1.0.0', '', 'one.two.three'):
            with self.assertRaises(ValueError):
                mm.bump_semver(bad)
        with self.assertRaises(ValueError):
            mm.bump_semver('1.0.0', 'huge')

    def test_only_the_version_value_is_edited(self):
        text = '{\r\n  "name": "X",\r\n  "tags": [ "A", "B" ],\r\n  "version": "1.4.9"\r\n}\r\n'
        new_text, old, new = mm.bump_version_text(text, 'minor')
        self.assertEqual((old, new), ('1.4.9', '1.5.0'))
        self.assertEqual(new_text, text.replace('1.4.9', '1.5.0'))  # formatting and CRLFs untouched

    def test_missing_version_field(self):
        with self.assertRaises(KeyError):
            mm.bump_version_text('{"name": "X"}')

    def test_which_mod_json_applies(self):
        have = set([R, R + '/Mods/Beta'])
        isfile = lambda f: f in have  # noqa: E731
        self.assertEqual(mm.mod_json_folder(R + '/Mods/Alpha', isfile), R)                    # root-style: nearest up
        self.assertIsNone(mm.mod_json_folder(R + '/Mods/Alpha', isfile, exact=True))          # nested: own folder only
        self.assertEqual(mm.mod_json_folder(R + '/Mods/Beta', isfile, exact=True), R + '/Mods/Beta')
        self.assertIsNone(mm.mod_json_folder('/Game/Nowhere', isfile))


class BumpNestedVersionTests(unittest.TestCase):
    AG = ('{\n  "repo_url": "",\n  "name": "Foo",\n  "dependencies": [\n    { "repo_url": "https://x/ArgonSDK-Mods", "version": ">=0.0.1" }\n  ],\n'
          '  "ag_mod": true,\n  "tags": [],\n  "version": "1.0.0"\n}\n')

    def test_a_dependency_version_before_the_top_level_one_is_left_alone(self):
        new_text, old, new = mm.bump_version_text(self.AG, 'patch')
        self.assertEqual((old, new), ('1.0.0', '1.0.1'))
        self.assertIn('">=0.0.1"', new_text)
        self.assertEqual(new_text, self.AG.replace('"version": "1.0.0"', '"version": "1.0.1"'))

    def test_only_a_nested_version_means_no_top_level_version(self):
        text = '{"name": "Foo", "dependencies": [{"version": "2.0.0"}]}'
        with self.assertRaises(KeyError):
            mm.bump_version_text(text)

    def test_strings_with_braces_and_escapes_do_not_confuse_the_scanner(self):
        text = '{"description": "a } \\" { \\\\ ]", "dependencies": [{"version": "9.9.9"}], "version": "3.0.0"}'
        new_text, old, new = mm.bump_version_text(text, 'minor')
        self.assertEqual((old, new), ('3.0.0', '3.1.0'))
        self.assertIn('"version": "9.9.9"', new_text)

    def test_a_key_named_version_inside_a_string_value_is_ignored(self):
        text = '{"note": "\\"version\\": \\"5.0.0\\"", "version": "1.1.1"}'
        self.assertEqual(mm.bump_version_text(text)[1:], ('1.1.1', '1.1.2'))


class MarkerNameTests(unittest.TestCase):
    def test_new_markers_are_prefixed_with_the_folder_name(self):
        self.assertEqual(mm.marker_name('/Game/Mods/AgMods/SomeMod'), 'SomeMod_ModMarker')
        self.assertEqual(mm.marker_name('/Game/Mods/AgMods/SomeMod/'), 'SomeMod_ModMarker')

    def test_the_legacy_name_is_still_the_plain_one(self):
        self.assertEqual(mm.MARKER_NAME, 'ModMarker')


class BundleVersionTests(unittest.TestCase):
    TOP = R + '/ModMarker'
    NEST = R + '/Mods/Alpha/ModMarker'
    OWN = R + '/Mods/Beta/ModMarker'
    EXT = '/Game/Mods/AgMods/Ext/ModMarker'

    def _setup(self):
        tree = mm.build_tree([self.TOP, self.NEST, self.OWN, self.EXT])
        paks = [{'root': self.TOP, 'markers': [self.TOP, self.NEST, self.EXT]}, {'root': self.OWN, 'markers': [self.OWN]},
                {'root': self.EXT, 'markers': [self.EXT]}]
        return tree, paks

    def lookup(self, folder, exact):
        return {R: '2.0.0', R + '/Mods/Alpha': '1.5.0', R + '/Mods/Beta': '3.0.0', '/Game/Mods/AgMods/Ext': '0.4.0'}.get(folder)

    def test_without_the_flag_every_marker_keeps_its_own_mod_json_version(self):
        tree, paks = self._setup()
        changes = mm.plan_marker_versions(tree, {}, self.lookup, paks, {}, [self.TOP, self.NEST, self.OWN])
        self.assertEqual(changes, {self.TOP: '2.0.0', self.NEST: '1.5.0', self.OWN: '3.0.0'})

    def test_the_flag_forces_the_top_version_onto_merged_and_bundled_markers_but_not_own_pak_ones(self):
        tree, paks = self._setup()
        changes = mm.plan_marker_versions(tree, {}, self.lookup, paks, {self.TOP: True}, [self.TOP, self.NEST, self.OWN])
        self.assertEqual(changes, {self.TOP: '2.0.0', self.NEST: '2.0.0', self.EXT: '2.0.0', self.OWN: '3.0.0'})

    def test_a_flagged_top_without_any_version_changes_nothing(self):
        tree, paks = self._setup()
        changes = mm.plan_marker_versions(tree, {}, lambda f, e: None, paks, {self.TOP: True}, [self.TOP, self.NEST])
        self.assertEqual(changes, {})

    def test_markers_already_on_the_version_are_left_out(self):
        tree, paks = self._setup()
        versions = {self.TOP: '2.0.0', self.NEST: '2.0.0'}
        changes = mm.plan_marker_versions(tree, versions, self.lookup, paks, {self.TOP: True}, [self.TOP, self.NEST])
        self.assertEqual(changes, {self.EXT: '2.0.0'})

    def test_actor_versions_follow_each_markers_effective_version(self):
        tree, _paks = self._setup()
        versions = {self.TOP: '2.0.0', self.NEST: '', self.EXT: '0.4.0'}
        classes = {self.TOP: ['/Game/R/A.A_C'], self.NEST: ['/Game/R/B.B_C'], self.EXT: ['/Game/Ext/C.C_C']}
        got = mm.plan_actor_versions(tree, versions, classes, [self.TOP, self.NEST, self.EXT])
        self.assertEqual(got, {'/Game/R/A.A_C': '2.0.0', '/Game/R/B.B_C': '2.0.0', '/Game/Ext/C.C_C': '0.4.0'})

    def test_a_marker_with_no_version_stamps_no_actors(self):
        tree, _paks = self._setup()
        self.assertEqual(mm.plan_actor_versions(tree, {}, {self.TOP: ['/Game/R/A.A_C']}, [self.TOP]), {})


if __name__ == '__main__':
    unittest.main()
