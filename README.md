# alexa-sync

Two-way sync between your Alexa shopping list and a list in Apple Reminders, on macOS.

Say "Alexa, add oat milk to my shopping list" and it shows up in Reminders a minute or two later. Tick it off in Reminders at the store and it's ticked off on Alexa too.

> **Unofficial.** Amazon has no public API for the shopping list anymore, so this uses the same private endpoints as the Alexa app. It can break whenever Amazon changes them. Use at your own risk.

## Requirements

- macOS 14+
- [uv](https://docs.astral.sh/uv/) (or pipx)
- [`remindctl`](https://github.com/openclaw/remindctl): `brew install steipete/tap/remindctl`, then `remindctl authorize`
- An Amazon account with **2-Step Verification using an authenticator app** (the login flow needs a code, not an SMS or push approval)

## Setup

```sh
uv tool install alexa-sync   # or: pipx install alexa-sync

alexa-sync login                     # email, password, authenticator code (one time)
alexa-sync doctor --list Groceries   # check everything is ready
alexa-sync run --list Groceries --dry-run   # preview the first sync
alexa-sync install --list Groceries  # sync every 2 minutes in the background
```

The Reminders list must already exist. `--list` defaults to `Groceries`.

Other commands: `alexa-sync run` (sync once), `alexa-sync uninstall` (stop the background job), `install --interval 60` (change how often it runs, in seconds).

## How it works

**Amazon login.** `login` registers your Mac as a device on your Amazon account, the same way the Alexa phone app does (via [aioamazondevices](https://github.com/chemelli74/aioamazondevices)). That returns a long-lived token, which is kept in your macOS Keychain. Your password and 2FA code are not stored. The token is used to get fresh session cookies whenever needed. If it ever stops working you'll get a macOS notification asking you to run `alexa-sync login` again. To revoke it, remove the device under *Manage Your Content and Devices* on Amazon.

**Sync.** Each run reads both lists and compares them with what they looked like after the last sync, so it can tell which side changed:

- Adds, check-offs, un-checks, renames and deletes go both ways.
- If the same item changed on both sides between syncs, Alexa wins.
- Items that are already completed when first seen aren't copied. This keeps years of old Alexa history out of Reminders.
- On the first sync, items with the same name on both sides are paired rather than duplicated.
- If a single pass would delete more than 5 items it stops and notifies you instead (override with `--max-deletes`).

**Files.**

| What | Where |
| --- | --- |
| Amazon device token | Keychain, service `alexa-sync` |
| Sync state | `~/Library/Application Support/alexa-sync/state.json` |
| Log | `~/Library/Logs/alexa-sync.log` |
| Background job | `~/Library/LaunchAgents/io.github.alexa-sync.plist` |

Only the default Alexa shopping list is synced. It has been tested with amazon.com; other Amazon regions should work, since the login library supports them, but are untested.

## Development

```sh
uv run pytest
uv run alexa-sync run --dry-run -v
```

## License

MIT
