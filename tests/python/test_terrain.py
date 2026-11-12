import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "scripts", "python"))
import terrain  # noqa: E402


def make_pbo(path, names, product=True, pad_entries=0):
    out = bytearray()
    if product:
        out += b"\0" + struct.pack("<5I", 0x56657273, 0, 0, 0, 0)
        out += b"prefix\0x\\y\0\0"
    for name in names:
        out += name.encode() + b"\0" + struct.pack("<5I", 0, 0, 0, 0, 0)
    for i in range(pad_entries):
        out += b"pad%d.txt\0" % i + struct.pack("<5I", 0, 0, 0, 0, 0)
    out += b"\0" + bytes(20)
    with open(path, "wb") as f:
        f.write(out)


WRP = "worlds\\1400_ad_ostmarschen\\1400_ad_ostmarschen.wrp"


class PboFileNamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name="a.pbo"):
        return os.path.join(self.tmp.name, name)

    def test_pbo_file_names_reads_all_entries_after_product_entry(self):
        names = ["config.bin", WRP]
        make_pbo(self.path(), names)
        self.assertEqual(terrain.pbo_file_names(self.path()), names)

    def test_pbo_file_names_without_product_entry(self):
        names = ["config.bin", WRP]
        make_pbo(self.path(), names, product=False)
        self.assertEqual(terrain.pbo_file_names(self.path()), names)

    def test_pbo_file_names_truncated_file_returns_partial_without_raising(self):
        make_pbo(self.path(), ["config.bin", WRP])
        with open(self.path(), "rb") as f:
            data = f.read()
        cut = data.index(b"config.bin") + len(b"config.bin\0") + 20 + 5
        with open(self.path(), "wb") as f:
            f.write(data[:cut])
        self.assertEqual(terrain.pbo_file_names(self.path()), ["config.bin"])


class HugeNameTests(unittest.TestCase):
    def test_pbo_file_names_stops_on_unterminated_huge_name(self):
        block = struct.pack("<5I", 0, 0, 0, 0, 0)
        data = (b"config.bin" + bytes(1) + block
                + b"A" * 5000 + bytes(1) + block
                + b"worlds/x/x.wrp" + bytes(1) + block
                + bytes(1) + bytes(20))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.pbo")
            with open(p, "wb") as f:
                f.write(data)
            self.assertEqual(terrain.pbo_file_names(p), ["config.bin"])


class WorldAvailableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        terrain._world_cache.clear()

    def mkdir(self, name):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        return d

    def test_world_found_in_workshop_copy_when_installed_folders_lack_it(self):
        a, b = self.mkdir("A"), self.mkdir("B")
        make_pbo(os.path.join(a, "cba.pbo"), ["config.bin"])
        make_pbo(os.path.join(b, "terrain.pbo"), ["config.bin", WRP])
        self.assertIs(terrain.world_available("1400_ad_ostmarschen", [a, b]), True)

    def test_world_found_beyond_512kb_header(self):
        a = self.mkdir("A")
        make_pbo(os.path.join(a, "big.pbo"), [WRP], pad_entries=40000)
        self.assertGreater(os.path.getsize(os.path.join(a, "big.pbo")), 512 * 1024)
        self.assertIs(terrain.world_available("1400_ad_ostmarschen", [a]), True)

    def test_world_missing_returns_false_when_pbos_scanned(self):
        a = self.mkdir("A")
        make_pbo(os.path.join(a, "cba.pbo"), ["config.bin"])
        self.assertIs(terrain.world_available("1400_ad_ostmarschen", [a]), False)

    def test_false_is_not_cached(self):
        a = self.mkdir("A")
        make_pbo(os.path.join(a, "cba.pbo"), ["config.bin"])
        self.assertIs(terrain.world_available("1400_ad_ostmarschen", [a]), False)
        make_pbo(os.path.join(a, "terrain.pbo"), ["config.bin", WRP])
        self.assertIs(terrain.world_available("1400_ad_ostmarschen", [a]), True)

    def test_nothing_scannable_returns_none(self):
        a = self.mkdir("A")
        missing = os.path.join(self.tmp.name, "nope")
        self.assertIsNone(terrain.world_available("foo", [a, missing]))

    def test_vanilla_world_true_without_scanning(self):
        self.assertIs(terrain.world_available("Altis", []), True)

    def test_match_is_case_insensitive_and_on_file_basename(self):
        a, b = self.mkdir("A"), self.mkdir("B")
        make_pbo(os.path.join(a, "x.pbo"), ["Worlds{0}Foo{0}FOO.WRP".format(chr(92))])
        make_pbo(os.path.join(b, "y.pbo"), ["worlds\\foobar\\foobar.wrp"])
        self.assertIs(terrain.world_available("foo", [a]), True)
        self.assertIs(terrain.world_available("foo", [b]), False)


if __name__ == "__main__":
    unittest.main()
