# SPDX-License-Identifier: AGPL-3.0-or-later
import unittest

from pakseal import policy


class PolicyTest(unittest.TestCase):
    def test_get_reads_nested_keys_and_defaults(self):
        value = {"network": True, "filePicker": {"openFile": True}, "memoryMaxMB": 512}
        self.assertTrue(policy.get(value, "network"))
        self.assertTrue(policy.get(value, "filePicker.openFile"))
        self.assertFalse(policy.get(value, "filePicker.saveFile"))
        self.assertFalse(policy.get(value, "clipboard.hostToApp"))
        self.assertEqual(policy.get(value, "memoryMaxMB"), 512)
        self.assertEqual(policy.get({}, "cpuQuota"), 0)

    def test_with_value_copies_and_keeps_unknown_fields(self):
        original = {"sessionBus": {"talk": [{"name": "org.example"}]}, "network": False}
        updated = policy.with_value(original, "network", True)
        self.assertFalse(original["network"])
        self.assertTrue(updated["network"])
        self.assertEqual(updated["sessionBus"], original["sessionBus"])
        self.assertIsNot(updated["sessionBus"], original["sessionBus"])

    def test_turning_a_requirement_off_turns_off_its_dependants(self):
        value = {"network": True, "hostNetwork": True, "displayX11": True, "clipboard": {"hostToApp": True, "appToHost": True}}
        value = policy.with_value(value, "network", False)
        self.assertFalse(value["hostNetwork"])
        value = policy.with_value(value, "displayX11", False)
        self.assertEqual(value["clipboard"], {"hostToApp": False, "appToHost": False})

    def test_file_picker_options_need_a_mode(self):
        value = {"filePicker": {"openFile": True, "persistent": True, "containingFolder": True}}
        value = policy.with_value(value, "filePicker.openFile", False)
        self.assertFalse(policy.get(value, "filePicker.persistent"))
        self.assertFalse(policy.get(value, "filePicker.containingFolder"))

        value = {"filePicker": {"saveFile": True, "openFile": True, "persistent": True, "containingFolder": True}}
        value = policy.with_value(value, "filePicker.openFile", False)
        self.assertTrue(policy.get(value, "filePicker.persistent"))
        self.assertFalse(policy.get(value, "filePicker.containingFolder"))

    def test_available_follows_requirements(self):
        persistent = next(t for g in policy.GROUPS for t in g.toggles if t.key == "filePicker.persistent")
        self.assertFalse(policy.available({}, persistent))
        self.assertTrue(policy.available({"filePicker": {"openFolder": True}}, persistent))

    def test_host_actions_are_added_and_removed_per_capability(self):
        value = policy.with_host_action({}, "cpak", "read", True)
        self.assertEqual(value["hostActions"], [{"provider": "cpak", "capabilities": ["read"]}])
        value = policy.with_host_action(value, "cpak", "exec", True)
        value = policy.with_host_action(value, "containers", "read", True)
        self.assertTrue(policy.host_action_enabled(value, "cpak", "exec"))
        self.assertEqual(value["hostActions"][0]["capabilities"], ["exec", "read"])
        value = policy.with_host_action(value, "cpak", "read", False)
        value = policy.with_host_action(value, "cpak", "exec", False)
        self.assertEqual(value["hostActions"], [{"provider": "containers", "capabilities": ["read"]}])
        value = policy.with_host_action(value, "containers", "read", False)
        self.assertNotIn("hostActions", value)

    def test_filesystem_paths_follow_cpak(self):
        for path in ("home", "home/.local/share/app", "host", "xdg-download", "/srv/data", "/mnt/a b"):
            self.assertTrue(policy.valid_filesystem_path(path), path)
        for path in ("", "/", "home/", "home/../etc", "home/a/../b", "home//a", "//srv", "/srv/", "/srv/./a", "relative", "xdg-unknown", "~/x"):
            self.assertFalse(policy.valid_filesystem_path(path), path)

    def test_filesystem_errors(self):
        entries = [{"path": "home", "access": "read-write"}]
        self.assertIsNone(policy.filesystem_error(entries, "xdg-music", "read-only"))
        self.assertIsNotNone(policy.filesystem_error(entries, "home", "read-only"))
        self.assertIsNotNone(policy.filesystem_error(entries, "host", "read-write"))
        self.assertIsNone(policy.filesystem_error(entries, "host", "read-only"))
        self.assertIsNotNone(policy.filesystem_error(entries, "home/..", "read-only"))

    def test_with_filesystem_drops_an_empty_list(self):
        value = policy.with_filesystem({"filesystem": [{"path": "home", "access": "read-only"}]}, [])
        self.assertNotIn("filesystem", value)

    def test_environment_errors(self):
        self.assertIsNone(policy.environment_error([], "MODE=dark"))
        self.assertIsNone(policy.environment_error([], "URL=postgres://db:5432/app"))
        self.assertIsNotNone(policy.environment_error([], "MODE"))
        self.assertIsNotNone(policy.environment_error([], "=x"))
        self.assertIsNotNone(policy.environment_error([], "1X=y"))
        self.assertIsNotNone(policy.environment_error(["MODE=light"], "MODE=dark"))

    def test_filesystem_risky(self):
        self.assertTrue(policy.filesystem_risky({"path": "host", "access": "read-only"}))
        self.assertTrue(policy.filesystem_risky({"path": "home", "access": "read-write"}))
        self.assertTrue(policy.filesystem_risky({"path": "home/.config", "access": "read-write"}))
        self.assertFalse(policy.filesystem_risky({"path": "home", "access": "read-only"}))
        self.assertFalse(policy.filesystem_risky({"path": "xdg-download", "access": "read-write"}))

    def test_describe_location(self):
        self.assertEqual(policy.describe_location("home/.config/app"), "~/.config/app")
        self.assertEqual(policy.describe_location("xdg-public-share"), "Public share folder")
        self.assertEqual(policy.describe_location("/srv"), "/srv")

    def test_legacy_in_use(self):
        self.assertFalse(policy.legacy_in_use({}, {"network": True}))
        self.assertTrue(policy.legacy_in_use({}, {"fsHostHome": True}))

    def test_application_uses_the_override_when_present(self):
        manifest = {"network": True}
        app = policy.Application.from_json({"origin": "github.com/a/b", "name": "B", "version": "main", "manifest": manifest, "override": None})
        self.assertFalse(app.customized)
        self.assertIs(app.policy, app.manifest)
        app = policy.Application.from_json({"origin": "github.com/a/b", "version": "main", "manifest": manifest, "override": {}})
        self.assertTrue(app.customized)
        self.assertEqual(app.policy, {})
        self.assertEqual(app.name, "github.com/a/b")

    def test_every_toggle_key_is_unique(self):
        keys = [t.key for g in policy.GROUPS for t in g.toggles] + [n.key for g in policy.GROUPS for n in g.numbers]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
