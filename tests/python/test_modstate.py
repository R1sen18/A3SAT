import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts", "python"))
import modstate  # noqa: E402

AU = "3020755032"


def make_mod(path, published_id=None, content="v1", key=None):
    os.makedirs(os.path.join(path, "addons"), exist_ok=True)
    with open(os.path.join(path, "addons", "main.pbo"), "w") as f:
        f.write(content)
    if published_id:
        with open(os.path.join(path, "meta.cpp"), "w") as f:
            f.write(f'protocol = 1;\npublishedid = {published_id};\n'
                    f'name = "Test";\ntimestamp = 5249116548785480542;\n')
    if key:
        os.makedirs(os.path.join(path, "keys"), exist_ok=True)
        with open(os.path.join(path, "keys", key), "w") as f:
            f.write("key")


def pbo(path):
    with open(os.path.join(path, "addons", "main.pbo")) as f:
        return f.read()


class ParseStateTests(unittest.TestCase):
    def test_flat_legacy_format(self):
        s = modstate.parse_state({AU: "@AU", "450814997": "@CBA"})
        self.assertEqual(s["mods"], {AU: "@AU", "450814997": "@CBA"})
        self.assertEqual(s["installed"], {})
        self.assertEqual(s["applied_at"], 0)

    def test_format_without_versions(self):
        s = modstate.parse_state({"applied_at": 1700000000, "mods": {AU: "@AU"},
                                  "names": {AU: "Antistasi Ultimate"}})
        self.assertEqual(s["mods"], {AU: "@AU"})
        self.assertEqual(s["names"], {AU: "Antistasi Ultimate"})
        self.assertEqual(s["installed"], {})
        self.assertEqual(s["applied_at"], 1700000000)

    def test_current_format_and_bad_records(self):
        s = modstate.parse_state({
            "applied_at": "x", "mods": {AU: "@AU"},
            "installed": {AU: {"folder": "@AU", "time_updated": "123"},
                          "1": {"folder": "@X", "time_updated": "nope"},
                          "2": "garbage", "3": {"time_updated": 5}}})
        self.assertEqual(s["installed"], {AU: {"folder": "@AU", "time_updated": 123}})
        self.assertEqual(s["applied_at"], 0)

    def test_garbage(self):
        for data in (None, [], "x", {"mods": "x"}):
            s = modstate.parse_state(data)
            self.assertEqual(s["mods"], {})
            self.assertEqual(s["installed"], {})


class InstalledVersionTests(unittest.TestCase):
    def test_record_must_match_folder(self):
        inst = {AU: {"folder": "@AU", "time_updated": 100}}
        self.assertEqual(modstate.installed_version(inst, AU, "@AU"), 100)
        self.assertIsNone(modstate.installed_version(inst, AU, "@AntistasiUltimateMod"))
        self.assertIsNone(modstate.installed_version(inst, "1", "@AU"))

    def test_merge_carries_over_updates_and_drops(self):
        prev = {AU: {"folder": "@AU", "time_updated": 100},
                "1": {"folder": "@Old", "time_updated": 50},
                "2": {"folder": "@Two", "time_updated": 70}}
        out = modstate.merge_installed(prev, {AU: "@AU", "2": "@TwoRenamed", "3": "@Three"},
                                       {"3": 300, "9": 900})
        self.assertEqual(out, {AU: {"folder": "@AU", "time_updated": 100},
                               "3": {"folder": "@Three", "time_updated": 300}})

    def test_merge_update_wins(self):
        prev = {AU: {"folder": "@AU", "time_updated": 100}}
        out = modstate.merge_installed(prev, {AU: "@AU"}, {AU: 200})
        self.assertEqual(out[AU]["time_updated"], 200)


ACF = r'''"AppWorkshop"
{
	"appid"		"107410"
	"SizeOnDisk"		"123"
	// comment
	"WorkshopItemsInstalled"
	{
		"3020755032"
		{
			"size"		"1"
			"timeupdated"		"1762000000"
			"manifest"		"55"
		}
		"450814997"
		{
			"timeupdated"		"1700000000"
		}
		"1"
		{
			"timeupdated"		"bad"
		}
	}
	"WorkshopItemDetails"
	{
		"3020755032"
		{
			"timeupdated"		"1799999999"
		}
	}
}
'''


class AcfTests(unittest.TestCase):
    def test_acf_path(self):
        p = modstate.acf_path_for(r"C:\steamcmd\steamapps\workshop\content\107410")
        self.assertEqual(os.path.normcase(p), os.path.normcase(
            os.path.join(r"C:\steamcmd\steamapps\workshop", "appworkshop_107410.acf")))

    def test_reads_installed_items_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "appworkshop_107410.acf")
            with open(path, "w") as f:
                f.write(ACF)
            self.assertEqual(modstate.read_cache_versions(path),
                             {AU: 1762000000, "450814997": 1700000000})

    def test_missing_or_broken(self):
        self.assertEqual(modstate.read_cache_versions("/nonexistent/x.acf"), {})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.acf")
            with open(path, "w") as f:
                f.write(ACF[: len(ACF) // 2])  # truncated mid-file
            self.assertEqual(modstate.read_cache_versions(path), {AU: 1762000000})

    def test_vdf_escapes(self):
        self.assertEqual(modstate.parse_vdf(r'"a" { "b" "x\"y" }'), {"a": {"b": 'x"y'}})


class PublishedIdTests(unittest.TestCase):
    def test_reads_meta_cpp(self):
        with tempfile.TemporaryDirectory() as d:
            make_mod(d, AU)
            self.assertEqual(modstate.read_published_id(d), AU)

    def test_no_meta(self):
        with tempfile.TemporaryDirectory() as d:
            make_mod(d)
            self.assertIsNone(modstate.read_published_id(d))


class DecideRefreshTests(unittest.TestCase):
    def test_known_versions(self):
        self.assertTrue(modstate.decide_refresh(AU, 100, 200).replace)
        self.assertFalse(modstate.decide_refresh(AU, 200, 200).replace)
        self.assertEqual(modstate.decide_refresh(AU, 200, 200).note, "")
        self.assertFalse(modstate.decide_refresh(AU, 300, 200).replace)

    def test_known_installed_ignores_steam(self):
        # Steam having something newer doesn't make an older cache copy usable
        self.assertFalse(modstate.decide_refresh(AU, 300, 200, steam_ts=999).replace)

    def test_unknown_cache_version_never_replaces(self):
        for installed in (None, 100):
            d = modstate.decide_refresh(AU, installed, None, steam_ts=50)
            self.assertFalse(d.replace)
            self.assertTrue(d.note)

    def test_legacy_unknown_installed(self):
        # The incident: folder from a setup applied before versions were
        # recorded; /download had fetched Steam's current version.
        self.assertTrue(modstate.decide_refresh(AU, None, 1762000000, 1762000000).replace)
        self.assertTrue(modstate.decide_refresh(AU, None, 1762000001, 1762000000).replace)
        stale = modstate.decide_refresh(AU, None, 1700000000, 1762000000)
        self.assertFalse(stale.replace)
        self.assertEqual(stale.kind, modstate.NOT_NEWER)
        self.assertIn("/download", stale.note)
        self.assertFalse(modstate.decide_refresh(AU, None, 1762000000, None).replace)

    def test_identity_mismatch(self):
        d = modstate.decide_refresh(AU, 100, 200, folder_id="999")
        self.assertFalse(d.replace)
        self.assertEqual(d.kind, modstate.MISMATCH)
        self.assertIn("999", d.note)
        self.assertFalse(modstate.decide_refresh(AU, 100, 200, cache_id="999").replace)
        self.assertTrue(modstate.decide_refresh(AU, 100, 200, folder_id=AU, cache_id=AU).replace)


class OutdatedTests(unittest.TestCase):
    def test_per_mod_compare(self):
        self.assertTrue(modstate.is_outdated(100, 200))
        self.assertFalse(modstate.is_outdated(200, 200))
        self.assertFalse(modstate.is_outdated(300, 200))

    def test_reapplied_old_install_is_outdated(self):
        # Old code compared against the global applied_at (today) and missed
        # this; the per-mod installed version (July) catches it.
        july, update, today = 1751328000, 1760000000, 1762900000
        self.assertTrue(modstate.is_outdated(july, update))
        self.assertLess(update, today)

    def test_unknowns(self):
        self.assertTrue(modstate.is_outdated(None, 200))
        self.assertFalse(modstate.is_outdated(None, None))
        self.assertFalse(modstate.is_outdated(100, None))

    def test_stale_cache_ids(self):
        steam = {"a": 200, "b": 200, "c": 200}
        cache = {"a": 100, "b": 200, "d": 1}
        self.assertEqual(modstate.stale_cache_ids(["a", "b", "c", "d"], steam, cache), ["a"])


class SwapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, "workshop", AU)
        self.dest = os.path.join(self.tmp.name, "server", "@AU")
        self.keys = os.path.join(self.tmp.name, "keys")
        os.makedirs(self.keys)

    def test_swap_replaces(self):
        make_mod(self.src, AU, "new")
        make_mod(self.dest, AU, "old")
        self.assertIsNone(modstate.swap_in_copy(self.src, self.dest))
        self.assertEqual(pbo(self.dest), "new")
        self.assertFalse(os.path.exists(self.dest + ".old"))

    def test_rename_failure_leaves_folder(self):
        make_mod(self.src, AU, "new")
        make_mod(self.dest, AU, "old")
        with mock.patch("modstate.os.rename", side_effect=PermissionError(32, "in use")):
            err = modstate.swap_in_copy(self.src, self.dest)
        self.assertIn("PermissionError", err)
        self.assertEqual(pbo(self.dest), "old")

    def test_copy_failure_restores_previous(self):
        make_mod(self.src, AU, "new")
        make_mod(self.dest, AU, "old")
        real_copytree = modstate.shutil.copytree

        def partial_copy(src, dest):
            os.makedirs(dest)
            with open(os.path.join(dest, "partial"), "w") as f:
                f.write("x")
            raise OSError("disk full")

        with mock.patch("modstate.shutil.copytree", side_effect=partial_copy):
            err = modstate.swap_in_copy(self.src, self.dest)
        self.assertEqual(err, "OSError")
        self.assertEqual(pbo(self.dest), "old")
        self.assertFalse(os.path.exists(os.path.join(self.dest, "partial")))
        self.assertFalse(os.path.exists(self.dest + ".old"))
        self.assertIs(modstate.shutil.copytree, real_copytree)

    def test_refresh_never_downgrades(self):
        make_mod(self.src, AU, "older")
        make_mod(self.dest, AU, "installed")
        r = modstate.refresh_folder(AU, self.src, self.dest, installed_ts=200,
                                    cache_ts=100, steam_ts=300, keys_dir=self.keys)
        self.assertFalse(r.replaced)
        self.assertEqual(pbo(self.dest), "installed")

    def test_refresh_same_version_untouched(self):
        make_mod(self.src, AU, "same")
        make_mod(self.dest, AU, "installed")
        r = modstate.refresh_folder(AU, self.src, self.dest, 200, 200)
        self.assertEqual(r, modstate.RefreshResult(False, "", modstate.CURRENT))
        self.assertEqual(pbo(self.dest), "installed")

    def test_refresh_never_swaps_in_another_mod(self):
        make_mod(self.src, "111", "other mod")
        make_mod(self.dest, AU, "installed")
        r = modstate.refresh_folder(AU, self.src, self.dest, 100, 200, keys_dir=self.keys)
        self.assertFalse(r.replaced)
        self.assertEqual(pbo(self.dest), "installed")
        make_mod(self.src, AU, "au")
        make_mod(self.dest, "111", "installed")
        self.assertFalse(modstate.refresh_folder(AU, self.src, self.dest, 100, 200).replaced)

    def test_refresh_newer_replaces_and_copies_keys(self):
        make_mod(self.src, AU, "12.4.1", key="au.bikey")
        make_mod(self.dest, AU, "11.9.12")
        r = modstate.refresh_folder(AU, self.src, self.dest, 100, 200, keys_dir=self.keys)
        self.assertTrue(r.replaced)
        self.assertEqual(pbo(self.dest), "12.4.1")
        self.assertTrue(os.path.isfile(os.path.join(self.keys, "au.bikey")))

    def test_refresh_legacy_needs_steam_latest(self):
        make_mod(self.src, AU, "cache")
        make_mod(self.dest, AU, "legacy")
        self.assertFalse(modstate.refresh_folder(AU, self.src, self.dest, None, 100, 200).replaced)
        self.assertEqual(pbo(self.dest), "legacy")
        self.assertTrue(modstate.refresh_folder(AU, self.src, self.dest, None, 200, 200).replaced)
        self.assertEqual(pbo(self.dest), "cache")

    def test_refresh_in_use_reports_and_keeps(self):
        make_mod(self.src, AU, "new")
        make_mod(self.dest, AU, "old")
        with mock.patch("modstate.os.rename", side_effect=PermissionError(32, "in use")):
            r = modstate.refresh_folder(AU, self.src, self.dest, 100, 200)
        self.assertFalse(r.replaced)
        self.assertEqual(r.kind, modstate.FAILED)
        self.assertIn("headless client", r.note)
        self.assertEqual(pbo(self.dest), "old")


if __name__ == "__main__":
    unittest.main()
