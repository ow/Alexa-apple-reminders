# alexa-sync

Two-way sync between the Alexa shopping list and an Apple Reminders list (default: `Groceries`).

- **Amazon:** logs in once as a virtual Alexa-app device (via [aioamazondevices](https://github.com/chemelli74/aioamazondevices)). The resulting refresh token lives in the macOS Keychain; your password and 2FA code are never stored.
- **Reminders:** via [`remindctl`](https://github.com/openclaw/remindctl) (`brew install steipete/tap/remindctl`).
- **Sync:** compares both sides against the last synced snapshot (`~/Library/Application Support/alexa-sync/state.json`), so adds, check-offs, renames and deletes flow both ways. If both sides changed the same item, Alexa wins. Items already completed when first seen aren't copied. A pass that would delete more than 5 items aborts and notifies you.

```sh
uv run alexa-sync login              # one time: email, password, authenticator code
uv run alexa-sync run --dry-run      # see what the first sync would do
uv run alexa-sync run                # sync once
uv run alexa-sync install            # every 2 minutes via launchd; logs in ~/Library/Logs/alexa-sync.log
uv run alexa-sync uninstall
```

If the Amazon registration ever stops working you'll get a macOS notification asking you to run `alexa-sync login` again.
