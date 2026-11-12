import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import mapmarker  # noqa: E402

R = '/Game/Mods/AgMods/Mod/'


class FindBaseMapsTests(unittest.TestCase):
    def test_sublevels_are_layers_not_base(self):
        refs = {R + 'Main': {R + 'Sub/Land', R + 'Sub/Sky'}, R + 'Sub/Land': {R + 'Sub/Sky'}, R + 'Sub/Sky': set()}
        out = mapmarker.find_base_maps(refs, root=R)
        self.assertEqual(out['base'], [R + 'Main'])
        self.assertEqual(sorted(out['layers']), [R + 'Sub/Land', R + 'Sub/Sky'])
        self.assertEqual(out['layers'][R + 'Sub/Sky'], [R + 'Main', R + 'Sub/Land'])

    def test_unrelated_maps_are_all_base(self):
        refs = {R + 'A': set(), R + 'B': set()}
        self.assertEqual(mapmarker.find_base_maps(refs, root=R)['base'], [R + 'A', R + 'B'])

    def test_excluded_folders_are_ignored_entirely(self):
        refs = {R + 'Main': set(), R + 'old/Main_v1': {R + 'Sub/Land'}, R + 'Sub/Land': set(),
                R + 'Reference/Art': set()}
        out = mapmarker.find_base_maps(refs, exclude_dirs=('old', 'reference'), root=R)
        # the excluded old map must not turn Land into a layer; Land stays a base map
        self.assertEqual(out['base'], [R + 'Main', R + 'Sub/Land'])
        self.assertEqual(out['excluded'], [R + 'Reference/Art', R + 'old/Main_v1'])

    def test_exclude_matches_folder_names_only_case_insensitively(self):
        refs = {R + 'Older': set(), R + 'OLD/Thing': set()}
        out = mapmarker.find_base_maps(refs, exclude_dirs=('old',), root=R)
        self.assertEqual(out['base'], [R + 'Older'])

    def test_self_reference_and_outside_scope_refs_ignored(self):
        refs = {R + 'A': {R + 'A', '/Game/Other/Map'}}
        out = mapmarker.find_base_maps(refs, root=R)
        self.assertEqual(out['base'], [R + 'A'])
        self.assertEqual(out['layers'], {})

    def test_mutual_cycle_is_kept_as_base_and_flagged(self):
        refs = {R + 'A': {R + 'B'}, R + 'B': {R + 'A'}}
        out = mapmarker.find_base_maps(refs, root=R)
        self.assertEqual(out['base'], [R + 'A', R + 'B'])
        self.assertEqual(out['cyclic'], [R + 'A', R + 'B'])
        self.assertEqual(out['layers'], {})

    def test_empty(self):
        out = mapmarker.find_base_maps({})
        self.assertEqual((out['base'], out['layers']), ([], {}))


class PendingTests(unittest.TestCase):
    def test_excludes_shipped_and_ignored_keeps_order(self):
        base = ['/a.a', '/b.b', '/c.c', '/d.d']
        self.assertEqual(mapmarker.pending(base, ['/b.b'], ['/d.d']), ['/a.a', '/c.c'])

    def test_nothing_pending_when_all_decided(self):
        self.assertEqual(mapmarker.pending(['/a.a'], ['/a.a'], []), [])
        self.assertEqual(mapmarker.pending(['/a.a'], [], ['/a.a']), [])

    def test_populate_then_ignore_flow_is_stable(self):
        base = ['/a.a', '/b.b', '/c.c']
        shipped, ignored = [], []
        shipped += mapmarker.pending(base, shipped, ignored)         # populate everything
        shipped = [p for p in shipped if p != '/c.c']                # owner removes c
        ignored += mapmarker.pending(base, shipped, ignored)         # ignore unused -> c
        self.assertEqual(ignored, ['/c.c'])
        self.assertEqual(mapmarker.pending(base, shipped, ignored), [])  # populate adds nothing


class LayersClosureTests(unittest.TestCase):
    def _refs(self, table):
        return lambda p: table.get(p, [])

    def test_transitive_layers_excluding_roots(self):
        refs = self._refs({'/Main': ['/Town'], '/Town': ['/Land'], '/Land': ['/Sky']})
        self.assertEqual(mapmarker.layers_closure(['/Main'], refs), ['/Land', '/Sky', '/Town'])

    def test_shared_layer_listed_once_and_other_roots_not_layers_of_themselves(self):
        refs = self._refs({'/A': ['/Sky'], '/B': ['/Sky', '/A']})
        self.assertEqual(mapmarker.layers_closure(['/A', '/B'], refs), ['/Sky'])

    def test_cycle_terminates(self):
        refs = self._refs({'/A': ['/B'], '/B': ['/A', '/C'], '/C': ['/B']})
        self.assertEqual(mapmarker.layers_closure(['/A'], refs), ['/B', '/C'])

    def test_no_roots(self):
        self.assertEqual(mapmarker.layers_closure([], self._refs({})), [])


class NamePatternTests(unittest.TestCase):
    def test_glob_excludes_by_map_name_case_insensitive(self):
        refs = dict((R + n, set()) for n in ('Main', 'Main_bak', 'Main_BAK2', 'Town_before_landscape', 'Keep'))
        out = mapmarker.find_base_maps(refs, root=R, exclude_patterns=('*_bak*', '*_before_*'))
        self.assertEqual(out['base'], [R + 'Keep', R + 'Main'])
        self.assertEqual(out['excluded'], [R + 'Main_BAK2', R + 'Main_bak', R + 'Town_before_landscape'])

    def test_pattern_matches_name_not_folder(self):
        refs = {R + 'bak_stuff/Real': set()}
        out = mapmarker.find_base_maps(refs, root=R, exclude_patterns=('*bak*',))
        self.assertEqual(out['base'], [R + 'bak_stuff/Real'])

    def test_excluded_by_pattern_does_not_make_layers(self):
        refs = {R + 'Main': set(), R + 'Main_bak': {R + 'Land'}, R + 'Land': set()}
        out = mapmarker.find_base_maps(refs, root=R, exclude_patterns=('*_bak',))
        self.assertEqual(out['base'], [R + 'Land', R + 'Main'])


class ExtraMapsTests(unittest.TestCase):
    def test_extra_maps_are_not_pending(self):
        self.assertEqual(mapmarker.pending(['/a.a', '/b.b', '/c.c'], ['/a.a'], [], ['/b.b']), ['/c.c'])


class AuditTests(unittest.TestCase):
    def _audit(self, **kw):
        args = dict(shipped=[], ignored=[], extra=[], stored_layers=[], computed_layers=[],
                    existing_worlds=set(), folder=R, pending_maps=[], references_of=lambda p: [])
        args.update(kw)
        return mapmarker.audit_findings(**args)

    def test_clean_marker_has_no_findings(self):
        self.assertEqual(self._audit(shipped=[R + 'A.A'], existing_worlds=set([R + 'A'])), [])

    def test_reports_stale_conflict_and_outside_folder(self):
        out = self._audit(shipped=[R + 'Gone.Gone', '/Game/Other/Map.Map'], ignored=[R + 'Gone.Gone'],
                          existing_worlds=set(['/Game/Other/Map']))
        text = '\n'.join(out)
        self.assertIn('Shipped map no longer exists: ' + R + 'Gone.Gone', text)
        self.assertIn('Both shipped and ignored: ' + R + 'Gone.Gone', text)
        self.assertIn('outside the marker folder: /Game/Other/Map.Map', text)

    def test_shipped_map_that_is_a_layer_of_another_shipped(self):
        refs = {R + 'Main': [R + 'Sky'], R + 'Sky': []}
        out = self._audit(shipped=[R + 'Main.Main', R + 'Sky.Sky'], existing_worlds=set([R + 'Main', R + 'Sky']),
                          references_of=lambda p: refs.get(p, []))
        self.assertTrue(any('already a layer of Main' in line and R + 'Sky.Sky' in line for line in out))

    def test_stale_layers_and_pending_are_reported(self):
        out = self._audit(stored_layers=[R + 'Old.Old'], computed_layers=[R + 'New.New'],
                          pending_maps=[R + 'X.X', R + 'Y.Y'])
        self.assertTrue(any('ShippedMapLayers is out of date (1 missing, 1 not needed)' in line for line in out))
        self.assertTrue(any('2 base map(s) are not shipped' in line for line in out))


class MergeRequiredTests(unittest.TestCase):
    def test_union_and_precedence(self):
        out = mapmarker.merge_required([
            {'shipped': ['/A'], 'extra': ['/E'], 'layers': ['/L', '/B']},
            {'shipped': ['/B'], 'extra': [], 'layers': ['/A', '/E', '/L2']},
        ])
        self.assertEqual(out['shipped'], ['/A', '/B'])
        self.assertEqual(out['extra'], ['/E'])
        self.assertEqual(out['layers'], ['/L', '/L2'])
        self.assertEqual(out['maps'], ['/A', '/B', '/E', '/L', '/L2'])

    def test_empty(self):
        self.assertEqual(mapmarker.merge_required([])['maps'], [])


if __name__ == '__main__':
    unittest.main()
