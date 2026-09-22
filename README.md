# Alexa x Apple Reminders Sync

Alexa is a terrible shopping list manager. Despite it being a core workflow, actually browsing the shopping list is poorly implemented in their app, and often forgets what it's doing when you are standing in the store. I wanted to be able to ask my Alexa at home to add items to the list, but not be forced to use Alexa's terrible mobile app when I am shopping. 

This tool lets you use your Alexa devices to add/remove shopping items, but then use Apple Reminders on your phone/macOS to interact with that list. The tool runs in the background on a Mac, checking every 2 minutes for new items and syncs them in both directions.

> [!NOTE]  
> This tool needs to run on an always-on Mac to work, as it interacts with Alexa's shopping list via API calls, but then needs direct Reminders CLI access to add/remove/edit items.

# How it works
Say "Alexa, add oat milk to my shopping list" and it shows up in Reminders a minute or two later. Tick it off in Reminders at the store and it's ticked off on Alexa too. Add something new via Siri to Reminders, and it appears in Alexa. 

> [!WARNING]  
> **Unofficial project.** Amazon has no public API for the shopping list, so this uses the same private endpoints as the Alexa app. It can and will break whenever Amazon changes them, though I'll do my best to keep it up to date. Use at your own risk!

## Requirements

- macOS 14+
- [uv](https://docs.astral.sh/uv/) (or pipx)
- The Reminders CLI tool: [`remindctl`](https://github.com/openclaw/remindctl): `brew install steipete/tap/remindctl`, then `remindctl authorize`
- An Amazon account with **2-Step Verification using an authenticator app** (the login flow needs a code, not an SMS or push approval)

## Setup

> [!TIP]
> **Your credentials stay on your Mac.** Your Amazon password and 2FA code are only used once during `alexa-sync login` and are never saved. What's kept is a device token (like the one the Alexa app gets), stored securely in your macOS Keychain. Nothing is sent anywhere except Amazon. You can revoke access anytime by removing the device under *Manage Your Content and Devices* on Amazon.

```sh
brew install steipete/tap/remindctl
remindctl authorize #Prompts for macOS Reminders access
uv tool install alexa-sync   # or: pipx install alexa-sync

alexa-sync login                     # email, password, authenticator code (one time)
alexa-sync doctor --list Groceries   # check everything is ready
alexa-sync run --list Groceries --dry-run   # preview the first sync
alexa-sync install --list Groceries  # sync every 2 minutes in the background
```

The Reminders list must already exist. `--list` defaults to `Groceries`.

Other commands: `alexa-sync run` (sync once), `alexa-sync uninstall` (stop the background job), `install --interval 60` (change how often it runs, in seconds).

## How it works

**Amazon login.** `login` registers your Mac as a "device" on your Amazon account, the same way the Alexa phone app does (via [aioamazondevices](https://github.com/chemelli74/aioamazondevices)). That returns a long-lived token, which is kept in your macOS Keychain. **Your password and 2FA code are only used once to sign in and are never stored.** The token is used to get fresh session cookies whenever needed. If it ever stops working you'll get a macOS notification asking you to run `alexa-sync login` again. To revoke it, remove the device under *Manage Your Content and Devices* on Amazon.

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

Only the default Alexa shopping list is synced. It has been tested with amazon.com, which seems to be where grocery lists are published regardless of location—it shows there for me despite being in Canada. Other Amazon regions should work, since the login library supports them, but are untested.

## Development

```sh
uv run pytest
uv run alexa-sync run --dry-run -v
```

## License

MIT
