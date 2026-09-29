# Pakseal

Pakseal is a permission manager for [cpak](https://github.com/Containerpak/cpak)
applications, in the spirit of Flatseal. It lists the installed applications,
shows the permissions each one runs with, and lets you change them. It is a
GTK 4 and libadwaita application and runs as a cpak package itself.

## How it works

cpak keeps a user override per installed version. An override replaces the
manifest policy as a whole, so Pakseal edits a complete copy of the policy and
writes all of it back. Permissions it does not show, such as filtered session
bus rules, are kept as they are. **Reset** removes the override, and the
application goes back to the policy its manifest asks for. Changes apply the
next time the application starts.

Inside cpak, Pakseal has no access to the files cpak keeps. It calls the
`cpak-host` shim, and the system broker forwards a fixed set of commands to the
host:

```sh
cpak permissions list --json
cpak permissions set --origin ORIGIN --package-version VERSION --policy - --json
cpak permissions reset --origin ORIGIN --package-version VERSION --json
```

Those commands and the `permissions` capability of the `cpak` host action
provider are not in upstream cpak yet. They are on the
`feat/permissions-host-action` branch of the cpak checkout in `cpak/`. The
broker refuses `set` and `reset` for the requesting application's own origin,
so Pakseal cannot widen its own sandbox. To change Pakseal's permissions, use
`cpak override` on the host.

Pakseal's manifest (`cpak.json`) asks for Wayland and
`{"provider": "cpak", "capabilities": ["permissions"]}` and nothing else. cpak
shows this capability at install time as "can change the permissions of other
cpak applications, including granting host access". That is accurate: a
permission manager is trusted with every application it can change.

## Development

The tests need only Python 3.11 or newer:

```sh
make test
```

To run the interface you need PyGObject, GTK 4 and libadwaita 1.5 or newer.
`make run-fake` uses `tools/fake-cpak`, which keeps sample applications in one
JSON file, so you don't need cpak. `make run` uses the `cpak` on your `PATH`.
That cpak must include the `permissions` command. Set `PAKSEAL_CPAK` to use
another binary.

## Packaging

A manifest v3 image has to be pinned by digest. Build the image, push it, and
write the digest into `cpak.json`:

```sh
make image
make pin
```

`IMAGE` defaults to `codeberg.org/nosini/pakseal`. If the package is published
from another origin, also change `PAKSEAL_ORIGIN` in the manifest's `env`.
Pakseal uses that value to recognise its own entry.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
