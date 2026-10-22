import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from image_state import init_db, pending_redirects, update_state
from item_names import build_artifacts, collect_names, generate_config, load_choices, resolve_images, write_name_index
from image_version import redirect_map
from item_buckets import write_item_buckets
from recipe_buckets import build_recipe_buckets, recipe_route
from routing_hash import ITEM_BUCKET_COUNT, bucket_id, short_hash


class NameRoutingTests(unittest.TestCase):
    def test_empty_nbt_and_chargeable_alias_do_not_overwrite_existing_key(self):
        import split_all
        items = [{"key": "mod:item", "rawKey": "mod:item"},
                 {"key": "mod:item_gLuB0gegBqh1aRe", "rawKey": "mod:item", "nbt": "{}"}]
        split_all._regress_nbt_only_items(items)
        self.assertEqual("mod:item_gLuB0gegBqh1aRe", items[1]['key'])
        tools = [{"key": "IC2:itemArmorBatpack:1"}, {"key": "IC2:itemArmorBatpack:26"},
                 {"key": "IC2:itemArmorBatpack"}]
        split_all._fix_ic2_chargeable_tools(tools)
        self.assertEqual(3, len(tools))

    def test_non_contiguous_nbt_variants_are_not_regressed(self):
        import split_all
        items = [{"key": "mod:item_hash1", "rawKey": "mod:item"},
                 {"key": "other:item", "rawKey": "other:item"},
                 {"key": "mod:item_hash2", "rawKey": "mod:item"}]
        with patch.object(split_all, '_load_nbt_regress_cache', return_value=({"mod:item_hash1", "mod:item_hash2"}, set())):
            split_all._regress_nbt_only_items(items)
        self.assertEqual(["mod:item_hash1", "other:item", "mod:item_hash2"], [item['key'] for item in items])

    def test_item_key_buckets_preserve_entire_records_and_duplicates(self):
        items = [{"key": "mod:item_hash", "translatedName": "重名", "index": "1/3", "nbt": "{a:1}"},
                 {"key": "other:item", "translatedName": "重名", "tooltips": "完整说明"}]
        items.append(dict(items[0]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_item_buckets(items, root)
            actual = []
            for path in root.rglob('*.json'):
                rows = json.loads(path.read_text('utf-8'))['items']
                self.assertTrue(all(bucket_id(row['key'], ITEM_BUCKET_COUNT) == path.parent.name for row in rows))
                actual.extend(rows)
            self.assertCountEqual(items, actual)
            write_item_buckets([], root)
            self.assertFalse(list(root.rglob('*.json')))

    def test_icon_prefix_lookup_preserves_original_first_match(self):
        from split_all import _merge_item_icons
        items = [{"shortKey": "mod:item_new"}, {"shortKey": "mod:exact"}]
        icons = {"mod:item_old": {"iconName": "first.png"},
                 "mod:item_other": {"iconName": "second.png"},
                 "mod:exact": {"iconName": "exact.png"}}
        _merge_item_icons(items, icons)
        self.assertEqual(["first.png", "exact.png"], [item['iconName'] for item in items])

    def test_large_name_list_spans_pages_and_old_pages_are_removed(self):
        keys = [f"example:item_{number:06d}" for number in range(1000)]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertGreater(write_name_index({"重名": keys}, root, max_bytes=2048), 1)
            found = []
            for path in root.rglob('*.json'):
                self.assertLessEqual(path.stat().st_size, 2048)
                self.assertEqual(bucket_id("重名"), path.parent.name)
                found.extend(json.loads(path.read_text('utf-8'))['names']['重名'])
            self.assertEqual(keys, sorted(found))
            write_name_index({"重名": keys[:1]}, root, max_bytes=2048)
            self.assertEqual(1, len(list(root.rglob('*.json'))))

    def test_existing_nbt_hash_and_unicode_bucket(self):
        self.assertEqual("gLuB0gegBqh1aRe", short_hash("{}"))
        self.assertEqual("OoSyzczEpOjdDBZ", short_hash("铁锭"))
        self.assertEqual("z0uV1u1ydGyUeKg", short_hash("minecraft:stone"))
        self.assertEqual("UA3EBiE1GzVnxP3", short_hash('{display:{Name:"中文"}}'))
        import hashlib
        for name in ("铁锭", "minecraft:stone", "A_B", "带有 NBT 的物品🧪"):
            digest = hashlib.sha256(name.encode("utf-8")).digest()
            self.assertEqual((digest[15] ^ digest[31]) % 64, int(bucket_id(name)))

    def test_choices_do_not_discard_name_candidates(self):
        items = [{"key": "a:one", "translatedName": "铁锭", "iconName": "icon_a.png"},
                 {"key": "b:two_hash", "translatedName": "铁锭", "iconName": "icon_b.png"}]
        names, groups = collect_names(items)
        self.assertEqual(["a:one", "b:two_hash"], names["铁锭"])
        redirects, unresolved, invalid = resolve_images(groups, {})
        self.assertFalse(redirects)
        self.assertIn("铁锭.png", unresolved)
        choices = {"铁锭.png": {"key": "b:two_hash"}}
        redirects, unresolved, invalid = resolve_images(groups, choices)
        self.assertEqual({"File:铁锭.png": "File:icon_b.png"}, redirects)
        self.assertFalse(unresolved or invalid)
        self.assertEqual(collect_names(items), collect_names(list(reversed(items))))
        redirects, _, invalid = resolve_images(groups, choices, ["铁锭.png"])
        self.assertFalse(redirects)
        self.assertIn("铁锭.png", invalid)

    def test_candidate_report_never_changes_choices(self):
        items = [{'key': 'gregtech:ingot', 'translatedName': '铜锭', 'iconName': 'gt.png'},
                 {'key': 'other:ingot', 'translatedName': '铜锭', 'iconName': 'other.png'}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, report = root / 'choices.json', root / 'report.json'
            config.write_text(json.dumps({'schemaVersion': 2, 'choices': {
                '铜锭.png': {'key': None, 'reason': 'suppress'}}}), 'utf8')
            original = config.read_bytes()
            result = generate_config(items, report, config)
            self.assertEqual(result, generate_config(items, report, config))
            self.assertEqual(result['candidates']['铜锭.png']['defaultKey'], 'gregtech:ingot')
            self.assertIsNone(result['candidates']['铜锭.png']['choice']['key'])
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(list(root.glob('*.bak')))
            with self.assertRaises(ValueError):
                generate_config(items, config, config)

    def test_preferred_choices_override_defaults_and_null_suppresses(self):
        items = [{'key': 'gregtech:ingot', 'translatedName': '铜锭', 'iconName': 'gt.png'},
                 {'key': 'other:ingot', 'translatedName': '铜锭', 'iconName': 'other.png'}]
        groups = collect_names(items)[1]
        self.assertEqual(resolve_images(groups, {}, items=items)[0], {'File:铜锭.png': 'File:gt.png'})
        self.assertEqual(resolve_images(groups, {'铜锭.png': {'key': 'other:ingot'}}, items=items)[0],
                         {'File:铜锭.png': 'File:other.png'})
        redirects, _, invalid = resolve_images(groups, {'铜锭.png': {'key': None}}, items=items)
        self.assertFalse(redirects or invalid)
        redirects, _, invalid = resolve_images(groups, {'铜锭.png': {'key': 'missing'}}, items=items)
        self.assertFalse(redirects)
        self.assertIn('铜锭.png', invalid)

    def test_gravitational_lens_preference_survives_addon_only_exports(self):
        base = {'key': 'gregtech:gt.metaitem.03:32277', 'translatedName': '引力透镜',
                'iconName': 'icon_gregtech_gt.metaitem.03_32277.png'}
        addon = {'key': 'TwistSpaceTechnology:MetaItem01:18', 'translatedName': '引力透镜',
                 'iconName': 'icon_TwistSpaceTechnology_MetaItem01_18.png'}
        choices = load_choices(Path(__file__).parent / 'config/preferred-items.json')
        choice = {'引力透镜.png': choices['引力透镜.png']}
        for items in ([base], [base, addon]):
            names, groups = collect_names(items)
            redirects, unresolved, invalid = resolve_images(groups, choice, items=items)
            self.assertEqual(redirects, {'File:引力透镜.png': 'File:' + base['iconName']})
            self.assertFalse(unresolved or invalid)
            self.assertEqual(set(names['引力透镜']), {item['key'] for item in items})
        # 附属独立发布时保留本体的中文别名，附属物品仍使用自身图标身份。
        redirects, _, invalid = resolve_images(collect_names([addon])[1], choice, items=[addon])
        self.assertFalse(redirects)
        self.assertEqual(invalid['引力透镜.png']['key'], base['key'])

    def test_build_defaults_use_full_nbt_records(self):
        items = [{'key': 'mod:item_a', 'rawKey': 'mod:item', 'nbt': '{a:1}',
                  'translatedName': '物品', 'iconName': 'a.png'},
                 {'key': 'mod:item_z', 'rawKey': 'mod:item', 'nbt': '{}',
                  'translatedName': '物品', 'iconName': 'z.png'}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = build_artifacts(iter(items), root / 'version', root / 'state', root, root / 'choices.json')
            self.assertFalse(report['unresolved'])
            self.assertIn('File:z.png', (root / 'state/image_redirect.resolved.csv').read_text('utf8'))

    def test_public_default_and_custom_path(self):
        import split_all
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(split_all.image_name_config(), Path(split_all.__file__).parent / 'config/preferred-items.json')
        with patch.dict(os.environ, {'IMAGE_NAME_CONFIG': 'custom.json'}):
            self.assertEqual(split_all.image_name_config(), Path('custom.json'))
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'choices.json'
            for data in ({'schemaVersion': 1, 'choices': {}},
                         {'schemaVersion': 2, 'choices': {'a.png': {'selectedKey': 'a'}}}):
                config.write_text(json.dumps(data), 'utf8')
                with self.assertRaises(ValueError):
                    load_choices(config)

    def test_build_is_local_and_config_change_invalidates_redirects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "dumps"
            source.mkdir()
            (source / "advitempanel.json").write_text("fixture", "utf-8")
            config = root / "choices.json"
            items = [{"key": "example:item_hash", "translatedName": "名称", "iconName": "icon_test.png"}]
            report = build_artifacts(items, root / "version", root / "state", source, config)
            item_page = root / "version/Items" / bucket_id("example:item_hash", ITEM_BUCKET_COUNT) / "0.json"
            self.assertEqual(json.loads(item_page.read_bytes())["items"], items)
            self.assertEqual(report["itemFileCount"], 1)
            self.assertFalse((root / "version/ItemBuckets").exists())
            self.assertFalse(config.exists(), "普通预处理不得自动生成或覆盖人工配置")
            index = root / "version/ItemNames" / bucket_id("名称") / "0.json"
            self.assertEqual({"名称": ["example:item_hash"]}, json.loads(index.read_text("utf-8"))["names"])
            with patch.dict(os.environ, {"DUMPS_DIR": str(source), "MOD_LOCAL_NAME": "GTNH", "IMAGE_NAME_CONFIG": str(config)}):
                self.assertEqual({"File:名称.png": "icon_test.png"}, redirect_map(str(root / "state/image_redirect.resolved.csv")))
                config.write_text('{"schemaVersion":2,"choices":{}}', "utf-8")
                with self.assertRaisesRegex(ValueError, "配置已修改"):
                    redirect_map(str(root / "state/image_redirect.resolved.csv"))

    def test_switching_config_path_requires_regeneration_even_with_same_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'dumps'
            source.mkdir()
            (source / 'advitempanel.json').write_text('fixture', 'utf8')
            first, second = root / 'a.json', root / 'b.json'
            for config in (first, second):
                config.write_text('{"schemaVersion":2,"choices":{}}', 'utf8')
            items = [{'key': 'mod:item', 'translatedName': '物品', 'iconName': 'item.png'}]
            build_artifacts(items, root / 'version', root / 'state', source, first)
            resolved = str(root / 'state/image_redirect.resolved.csv')
            with patch.dict(os.environ, {'DUMPS_DIR': str(source), 'MOD_LOCAL_NAME': 'GTNH',
                                         'IMAGE_NAME_CONFIG': str(first)}):
                self.assertTrue(redirect_map(resolved))
                os.environ['IMAGE_NAME_CONFIG'] = str(second)
                with self.assertRaisesRegex(ValueError, '配置路径已切换'):
                    redirect_map(resolved)
                build_artifacts(items, root / 'version', root / 'state', source, second)
                self.assertTrue(redirect_map(resolved))

    def test_multiple_aliases_and_legacy_uploaded_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "images.db"
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("CREATE TABLE images(icon_name TEXT PRIMARY KEY,redirect_from TEXT,icon_state TEXT,redirect_state TEXT)")
                conn.execute("INSERT INTO images VALUES ('icon.png','File:旧名.png','已上传','已上传')")
            init_db(db)
            aliases = {"File:旧名.png": "icon.png", "File:新名.png": "icon.png"}
            update_state(db, {"icon.png"}, aliases)
            self.assertEqual([("File:新名.png", "icon.png")], pending_redirects(db, aliases))
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM redirects").fetchone()[0])
                self.assertEqual("已上传", conn.execute("SELECT icon_state FROM images").fetchone()[0])

    def test_legacy_collision_requires_reapplying_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            db = Path(temporary) / "images.db"
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("CREATE TABLE images(icon_name TEXT PRIMARY KEY,redirect_from TEXT,icon_state TEXT,redirect_state TEXT)")
                conn.executemany("INSERT INTO images VALUES (?, 'File:重名.png', '已上传', '已上传')", [("a.png",), ("b.png",)])
            update_state(db, {"a.png", "b.png"}, {"File:重名.png": "a.png"})
            self.assertEqual([("File:重名.png", "a.png")], pending_redirects(db, {"File:重名.png": "a.png"}))

    def test_recipe_route_uses_only_unique_final_key_and_preserves_records(self):
        recipe = {"index": "0/0", "inputItems": [{"key": "input"}],
                  "outputItems": [[{"key": "exact_hash", "nbt": "{unmatched:1}"},
                                   {"key": "exact_hash", "queryKey": "bee:species"}]], "otherItems": []}
        self.assertEqual(bucket_id("exact_hash"), recipe_route(recipe))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Recipes/gregtech/GTNEIDefaultHandler_assembler/0.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({"type": "fixture", "recipes": [recipe]}), "utf-8")
            second = {**recipe, "index": "1/0"}
            source.with_name("1.json").write_text(json.dumps({"type": "fixture", "recipes": [second]}), "utf-8")
            report = build_recipe_buckets(root)
            self.assertEqual(2, report["writtenRecipes"])
            paths = list(source.parent.rglob("*.json"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].parent.name, bucket_id("exact_hash"))
            data = json.loads(paths[0].read_bytes())
            self.assertEqual(data["recipes"], [recipe, second])
            self.assertEqual(data["routing"]["identityFields"], ["key"])

    def test_multiple_final_output_keys_preserve_flat_pool(self):
        recipe = {"index": "0/0", "outputItems": [[{"key": "a"}, {"key": "b"}]]}
        self.assertIsNone(recipe_route(recipe))
        self.assertIsNone(recipe_route({"outputItems": [{"queryKey": "bee:species"}]}))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            handler = "gregtech/GTNEIDefaultHandler_assembler"
            source = root / "Recipes" / handler
            source.mkdir(parents=True)
            original = json.dumps({"recipes": [recipe]}).encode()
            for page in (0, 1):
                (source / f"{page}.json").write_bytes(original)
            report = build_recipe_buckets(root)
            self.assertIn(handler, report["skipped"])
            self.assertEqual(report["writtenRecipes"], 0)
            self.assertTrue(all(path.read_bytes() == original for path in source.glob("*.json")))
            self.assertEqual(len(list(source.glob("*.json"))), 2)

    def test_only_selected_multiple_file_handlers_and_hammer_shared(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            single = {"outputItems": [{"key": "a"}]}
            multiple = {"outputItems": [{"key": "a"}, {"key": "b"}]}
            for handler, recipes in (("GTNEIDefaultHandler_hammer", [single, multiple]),
                                     ("GTNEIDefaultHandler_macerator", [single]),
                                     ("GTNEIDefaultHandler_assembler", [multiple])):
                for page in (0, 1):
                    path = root / "Recipes/gregtech" / handler / f"{page}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"recipes": recipes}), "utf-8")
            report = build_recipe_buckets(root)
            self.assertEqual(["gregtech/GTNEIDefaultHandler_hammer"], list(report["handlers"]))
            self.assertEqual(2, report["sharedRecipes"])
            shared = root / "Recipes/gregtech/GTNEIDefaultHandler_hammer/shared/0.json"
            self.assertEqual(2, len(json.loads(shared.read_text("utf-8"))["recipes"]))
            self.assertTrue((root / "Recipes/gregtech/GTNEIDefaultHandler_macerator/0.json").exists())
            self.assertTrue((root / "Recipes/gregtech/GTNEIDefaultHandler_assembler/0.json").exists())
            self.assertFalse((root / "Recipes/gregtech/GTNEIDefaultHandler_hammer/0.json").exists())
            self.assertFalse((root / "RecipeBuckets").exists())



class DefaultImageRulesTest(unittest.TestCase):
    def test_nbt_representative_is_stable_and_scoped(self):
        a = {'key': 'mod:item_a', 'rawKey': 'mod:item', 'nbt': '{a:1}'}
        b = {'key': 'mod:item_b', 'rawKey': 'mod:item', 'nbt': '{a:2}'}
        self.assertEqual(self.choose(b, a), a['key'])
        self.assertEqual(self.choose(a, b), a['key'])
        self.assertEqual(self.choose(a, b, {'key': 'mod:item'}), 'mod:item')
        self.assertIsNone(self.choose(a, {'key': 'mod:other_z', 'rawKey': 'mod:other', 'nbt': '{a:2}'}))

    def test_current_controller_and_one_slot_battery(self):
        a = {'key': 'gregtech:gt.blockmachines:1', 'tooltips': '§4已弃用，此控制器将移除'}
        b = {'key': 'gregtech:gt.blockmachines:2', 'tooltips': '新控制器'}
        self.assertEqual(self.choose(a, b), b['key'])
        a.update(translatedName='低压电池箱', tooltips='低压电池箱<br><br>16个槽位<br>容量')
        b.update(translatedName='低压电池箱', tooltips='低压电池箱<br><br>1个槽位<br>容量')
        self.assertEqual(self.choose(a, b), b['key'])
        b['tooltips'] = '4个槽位'
        self.assertIsNone(self.choose(a, b))

    def test_fluid_owner_is_not_display_item_mod(self):
        a = {'key': 'fluid.a', 'mod': 'GregTech', 'registeredBy': 'TConstruct'}
        b = {'key': 'fluid.b', 'mod': 'GregTech', 'registeredBy': 'gregtech'}
        self.assertEqual(self.choose(a, b), b['key'])
        del b['registeredBy']
        self.assertIsNone(self.choose(a, b))

    def choose(self, *items):
        from item_names import default_image_candidate
        return default_image_candidate(list(items))[0]

    def test_nbt_and_damage_are_scoped_to_same_item(self):
        self.assertEqual(self.choose({'key': 'a:x_empty', 'rawKey': 'a:x', 'nbt': '{}'},
                                     {'key': 'a:x_full', 'rawKey': 'a:x', 'nbt': '{a:1}'}), 'a:x_empty')
        self.assertEqual(self.choose({'key': 'a:x'}, {'key': 'a:x:3'},
                                     {'key': 'a:x_hash', 'rawKey': 'a:x', 'nbt': '{a:1}'}), 'a:x')
        self.assertIsNone(self.choose({'key': 'a:x'}, {'key': 'a:y:3'}))
        self.assertIsNone(self.choose({'key': 'a:x:2'}, {'key': 'a:x:3'}))

    def test_fluid_and_cross_mod_priority(self):
        self.assertEqual(self.choose({'key': 'fluid.water', 'nbt': '{display:1}'},
                                     {'key': 'gregtech:water'}, {'key': 'minecraft:water'}), 'fluid.water')
        self.assertEqual(self.choose({'key': 'gregtech:ingot:7'}, {'key': 'other:ingot'}),
                         'gregtech:ingot:7')
        self.assertIsNone(self.choose({'key': 'fluid.a'}, {'key': 'fluid.b'}))

    def test_stone_ore_preserves_other_metadata_meanings(self):
        self.assertEqual(self.choose({'key': 'gregtech:gt.blockores2:35'},
                                     {'key': 'gregtech:gt.blockores2:1035'},
                                     {'key': 'gregtech:gt.blockores3:35'}), 'gregtech:gt.blockores2:35')
        self.assertIsNone(self.choose({'key': 'gregtech:gt.blockores2:35'},
                                     {'key': 'gregtech:gt.blockores2:8035'}))
        self.assertEqual(self.choose({'key': 'gregtech:gt.blockores2:16035'},
                                     {'key': 'gregtech:gt.blockores2:17035'}), 'gregtech:gt.blockores2:16035')


if __name__ == "__main__":
    unittest.main()
