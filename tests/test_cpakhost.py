# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest

from pakseal.cpakhost import CpakError, CpakHost

FAKE_CPAK = textwrap.dedent(
    """\
    import json, sys
    log = sys.argv[1]
    arguments = sys.argv[2:]
    stdin = sys.stdin.read()
    with open(log, "a") as handle:
        handle.write(json.dumps({"arguments": arguments, "stdin": stdin}) + "\\n")
    entry = {"origin": "github.com/example/app", "name": "App", "version": "main",
             "manifest": {"network": True}, "override": None}
    if arguments[:2] == ["permissions", "list"]:
        print(json.dumps([entry]))
    elif arguments[:2] == ["permissions", "set"]:
        if "fail" in stdin:
            print("Error: filesystem host scope can only be read-only", file=sys.stderr)
            sys.exit(1)
        entry["override"] = json.loads(stdin)
        print(json.dumps(entry))
    elif arguments[:2] == ["permissions", "reset"]:
        print(json.dumps(entry))
    else:
        print("not json")
    """
)


class CpakHostTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = os.path.join(directory.name, "log")
        script = os.path.join(directory.name, "cpak.py")
        with open(script, "w") as handle:
            handle.write(FAKE_CPAK)
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        self.host = CpakHost([sys.executable, script, self.log])

    def calls(self):
        with open(self.log) as handle:
            return [json.loads(line) for line in handle]

    def test_list(self):
        apps = self.host.list()
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].key, ("github.com/example/app", "main"))
        self.assertTrue(apps[0].policy["network"])
        self.assertEqual(self.calls()[0]["arguments"], ["permissions", "list", "--json"])

    def test_set_sends_the_complete_policy_on_stdin(self):
        app = self.host.set("github.com/example/app", "main", {"network": False, "socketWayland": True})
        self.assertEqual(app.override, {"network": False, "socketWayland": True})
        call = self.calls()[0]
        self.assertEqual(
            call["arguments"],
            ["permissions", "set", "--origin", "github.com/example/app", "--package-version", "main", "--policy", "-", "--json"],
        )
        self.assertEqual(json.loads(call["stdin"]), {"network": False, "socketWayland": True})

    def test_reset(self):
        app = self.host.reset("github.com/example/app", "main")
        self.assertFalse(app.customized)
        self.assertEqual(
            self.calls()[0]["arguments"],
            ["permissions", "reset", "--origin", "github.com/example/app", "--package-version", "main", "--json"],
        )

    def test_errors_carry_the_cpak_message(self):
        with self.assertRaises(CpakError) as raised:
            self.host.set("github.com/example/app", "main", {"fail": True})
        self.assertEqual(str(raised.exception), "filesystem host scope can only be read-only")

    def test_output_that_is_not_json_is_an_error(self):
        with self.assertRaises(CpakError):
            self.host._run(["other"])


if __name__ == "__main__":
    unittest.main()
