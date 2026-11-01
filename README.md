# OwO Farmer

A careful OwO-bot grinder for a Discord account. One Python file, one config file, a live stats page you can
open in your browser, and a stop-first attitude: when OwO shows a captcha or a ban notice it stops and tells
you instead of trying to get around it.

> **Read this first.** Running a program on a user account is a "self-bot", and that breaks
> [Discord's Terms of Service](https://discord.com/terms) and OwO's rules. Accounts get restricted and
> banned for it. Nothing in this script makes that safe, and it does not try to hide that it is
> automated: the random timing and the breaks below make it behave less like a stopwatch, not safe. Use it on
> an account you can afford to lose, in a private channel. Not affiliated with Discord or OwO.

## What it does

| | |
|---|---|
| **Grinding** | `owo`, `hunt`, `battle`, `pray` / `curse`, `daily`, plus optional `cookie`, `sell`, `sac`, `lootbox`, `crate`, balance checks and your own custom commands |
| **Cooldowns** | Every command waits a random time inside the range you set. Anything below OwO's real cooldown is raised automatically, so a typo cannot turn it into a spammer |
| **Hunt and battle timing** | After OwO's 15 s cooldown ends, the next one goes out either right away (35 % of the time, within about a second) or 2 to 3 s later. Both numbers are settings |
| **Breaks** | After every 10 minutes of running it rests for 3 minutes, then carries on. Both numbers are settings (0 turns it off) |
| **Gems** | Optional. Reads the charge bar next to each gem in your hunt replies. The moment a gem shows `[0/50]` it fetches your inventory and uses the next one, so the next hunt already has it |
| **Daily** | Claimed once a day. The reset time is learned from OwO's own replies and remembered between runs |
| **Gambling** | Optional coinflip or slots, with flat or martingale bets, a max bet, balance-aware sizing, stop-loss, take-profit and a daily loss limit that survives restarts |
| **Reads OwO's replies** | Notices captchas, bans, "slow down" warnings, empty wallets, and commands OwO never answered |
| **Captcha DMs** | Optional. A second account DMs you and pings every 30 s until you reply `stop` |
| **Stats page** | A read-only web page with everything: counts, schedule, gem bars, breaks, gambling, alerts, events |
| **Terminal dashboard** | The same information inside your terminal, with hotkeys |

### What it deliberately does not do

* **It never solves captchas.** It stops, alerts you, and carries on after *you* solve it.
* No fake typing, filler chat or other "act human" noise.
* No multi-account running, giveaways, huntbot or quests. Use custom commands if you want something extra.

## Quick start

You need Python 3.10 or newer, the ID of a channel to farm in, and your account token.

**Windows:** double-click `start.bat`. **Linux / macOS / Termux:** `./start.sh`.

The first run builds a private Python environment, installs the requirements and writes `config.toml`. That can
take a few minutes: keep the window open. (On Windows the environment lives in `%LOCALAPPDATA%\owo-farmer`, not in
this folder, because Desktop and Documents are usually synced by OneDrive and building it there is very slow;
set `OWO_FARMER_VENV` to choose another place. If a setup is interrupted, the next run repairs it by itself.) Then:

1. Open `config.toml` and set `farm.channel_id`. In Discord: *Settings → Advanced → Developer Mode*, then
   right-click the channel and *Copy Channel ID*.
2. Give the script your token. The safest way is an environment variable, so it never sits in a file:

   ```powershell
   $env:DISCORD_TOKEN = "your-token"      # this terminal only
   ```

   You can also paste it into `account.token` in `config.toml` (the file is git-ignored).
3. Check everything without connecting: `start.bat --check`
4. Preview what it will do, offline, in a few seconds: `start.bat --simulate 120`
5. Run it: `start.bat`. It prints the stats page address, `http://127.0.0.1:8787` by default.

> **Treat tokens like passwords.** Anyone holding one controls that account. Never paste a token into a
> chat, a message, an issue or a git repository. If you already did, change that account's password: that
> invalidates the old token. This script never prints tokens and removes them from its log file.

Without the helper scripts:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows;  .venv/bin/pip on Linux/macOS
.venv/Scripts/python owo_farmer.py --init
.venv/Scripts/python owo_farmer.py
```

> It must be **discord.py-self**, not the official `discord.py`. They share the import name `discord` and
> cannot be installed together. The script checks this on start-up and tells you how to fix it.

## The stats page

Open the address it prints (default `http://127.0.0.1:8787`). It refreshes every 2 seconds and shows: messages
sent, hunts, battles, prays, animals caught, XP gained, cowoncy (and the change since the start), captchas,
slow-downs, a live schedule with countdowns, a charge bar for each gem, the break countdown, gambling results,
the alert state, the last events and the last commands. It is **read-only**: it cannot change or control
anything, and it never contains tokens or passwords. It works on a phone-sized screen too.

* By default only this computer can open it (`host = "127.0.0.1"`).
* To open it from your phone or another PC, or when the script runs on a server, set `host = "0.0.0.0"` and
  a `password` of at least 8 characters under `[dashboard]`. The script refuses to start otherwise. The password
  is sent unencrypted over plain HTTP, so keep that to a network you trust, or put it behind an HTTPS tunnel
  or reverse proxy.
* Change the `port` if 8787 is taken. If it cannot start, the farm carries on without it.

## Captcha DMs from a second account

When OwO shows a captcha, a **second Discord account** can DM you and ping you every 30 seconds (up to 60
times) until you reply `stop`. It also stops when OwO says you are verified, or when you press `c`.

1. Use a second account that can message you: it must be your friend, or share a server with you with DMs open.
2. Give the script that account's token as an environment variable:

   ```powershell
   $env:ALERT_ACCOUNT_TOKEN = "token-of-the-second-account"
   ```
3. In `config.toml`:

   ```toml
   [alerts.dm]
   enabled = true
   user_id = 123456789012345678     # YOUR user ID: the one that gets the DMs
   ```
4. `start.bat --check` shows whether the alert token was found.

How it works: the farming account is a party to that DM, so it sees you type `stop` itself. The alert account
only logs in over HTTP to send. If you reply "stop" the pings end and stay quiet until the next captcha.
A ban or a fatal stop sends one DM instead of a series. The pings last while the script is running; with the
default `captcha_give_up_minutes = 20` the script exits after 20 minutes of an unsolved captcha (set it to 0 to
keep waiting).

The alert account is also a self-bot account, so it carries the same risk. Use a throwaway one, never your main.
If the DMs fail three times in a row (for example because DMs are closed), the script switches them off and says why.

## Controls

| Key | Action |
|---|---|
| `p` | pause / resume |
| `c` | "I solved the captcha", resume now (also ends the DM pings) |
| `g` | switch gambling off / on |
| `q` or Ctrl+C | quit (a summary is printed) |

## Configuration

Everything is in `config.toml` and every option is explained in `config.example.toml`. Ranges are
`[min, max]` seconds. A few that matter most:

| Setting | Meaning |
|---|---|
| `farm.channel_id` | **Required.** A private channel that only you and OwO use is best |
| `farm.prefix` | `"owo"` or `"w"` |
| `farm.gap` | pause between *any* two messages (never below 2 s) |
| `farm.max_messages_per_minute` | hard ceiling, whatever else is set (never above 20) |
| `farm.extra_names`, `farm.trust_channel` | OwO writes your name (or your `@mention`) in its replies and the script finds its replies by it. If your name is only emoji it cannot, so it falls back to trusting the whole channel and warns you. Use a private channel, or list extra names here |
| `commands.<name>.enabled` / `.cooldown` | switch each command and set its range |
| `commands.hunt` / `commands.battle`: `instant_chance`, `delay` | the share of the time the next one goes out right away, and the extra wait otherwise |
| `commands.pray.targets` / `.curse_targets` | user IDs to pray for / curse; if both are set it alternates |
| `gems.enabled` | automatic gem upkeep (`types`, `tiers`, `lowest_first`) |
| `gamble.*` | see below |
| `safety.break_every_minutes` / `break_minutes` | the rest cycle; set both to 0 to switch it off |
| `safety.quiet_hours` | e.g. `["01:00-07:30"]`, local time, wraps midnight |
| `safety.max_runtime_minutes` / `max_commands` | stop after this much |
| `alerts.dm.*` | the captcha DMs, see above |
| `alerts.webhook_url` | optional; must be a Discord webhook URL |
| `dashboard.*` | the stats page: `enabled`, `host`, `port`, `password` |
| `ui.terminal_dashboard` | the dashboard inside the terminal |
| `[[custom]]` | your own timed commands (`text`, `cooldown`, `prefix`, and `expects_reply = true` if OwO always answers it) |

Minimum cooldowns the script enforces: hunt and battle 15.5 s, owo 10.5 s, pray 301 s, crate 30.5 s,
gambling 15.5 s. Wrong or misspelled options are rejected with a "did you mean" hint, not ignored.

### Gems

OwO shows each gem's charge next to it in the hunt reply, for example
`hunt is empowered by <:cgem1:…>[1/25] <:cgem3:…>[46/50] <:cgem4:…>[0/50]`. The script reads those bars. A gem
reading `[0/N]` is spent, so its replacement is fetched and used before the next hunt; a gem with charge left is
never doubled up (OwO does not allow that). With the offline simulator, 375 hunts over two hours had only the
very first hunt without a gem, against 15 when the script waits for a gem to disappear instead. If your inventory
runs out of a type, it checks again after `empty_retry_minutes`.

### Gambling

Off by default. The house wins on average (slots return roughly 86 cents per cowoncy bet) and no betting
pattern changes that. Martingale only changes *how* you lose, with a small chance of a very large loss.
The limits exist to cap the damage:

* `max_bet`, and never more than `max_balance_fraction` of your known balance, always leaving `keep_balance`
* `stop_loss` and `take_profit` per session, `daily_loss_limit` per day (remembered across restarts, so
  restarting cannot reset it), `max_bets`
* martingale resets to `base_bet` after `max_streak` losses in a row
* the last bet before a limit is trimmed so the limit is not overshot
* if OwO says you are out of cowoncy, gambling halts and the balance is re-read

## What happens when something goes wrong

| Situation | Behaviour |
|---|---|
| **Captcha** (channel text, DM, a `<@you>` mention, or a Verify button) | All sending stops. Bell, beeps, webhook and (if set up) DM pings, with reminders every 45 s. It resumes 20 to 60 s after OwO says you are verified (or press `c`). Exits after 20 minutes unsolved |
| **Ban notice** | Exits at once with code 2 and alerts (one DM if DM alerts are on) |
| **"Slow down" cooldown notice** | That command is pushed back until its cooldown ends. Four notices in 10 minutes pause everything for 2 to 5 minutes |
| **"Too fast for me"** | Backs off 8 to 15 s and halves its speed for 3 minutes |
| **OwO stops answering** | After 5 unanswered commands it pauses (3, 6, then 9 minutes) and alerts; after that it gives up |
| **Out of cowoncy** | Gambling halts for 30 minutes; hunting retries in 10 |
| **Discord blocks the send** | "Cannot send here" or "account needs a captcha" stops the run with a clear message; rate limits pause |
| **You type a command yourself** | That command's timer resets so nothing is sent twice |
| **Scheduled break / quiet hours / limits** | Pauses and resumes by itself / stops cleanly |

Exit codes: `0` normal, `1` config problem, `2` safety stop (ban, unsolved captcha, OwO silent),
`3` could not log in, `4` cannot use the channel.

## Command line

```
python owo_farmer.py                     run
python owo_farmer.py --init [--force]    write config.toml
python owo_farmer.py --check             validate the config, no login
python owo_farmer.py --simulate 120      run 120 minutes against a fake OwO, offline
python owo_farmer.py --simulate 90 --sim-captcha 30      ...with a captcha at minute 30
python owo_farmer.py --plain             plain log lines instead of the terminal dashboard
python owo_farmer.py -c other.toml       use another config file
```

## Tests

```
python -m unittest -v
```

More than 150 tests run offline with no account: the message parsers (using OwO's real reply formats, including
the gem bars and the `<@you>` captcha), config validation, gambling and gem logic, the break cycle and hunt
timing, the DM alerts (pinging, `stop`, re-arming, failures), the stats page (including its password, its
refusal of foreign host names, and a check that no secret ever appears in it), and the scheduler against a
simulated OwO over hours of fake time (cooldown spacing, rate caps, captcha, ban, slow-downs, silent OwO, quiet
hours, send failures). The Discord connection layer is exercised with fake objects, and the stats page was
also checked in a real browser, on a desktop and a phone-sized screen. **What no offline test can prove is how a
real account behaves against real Discord and OwO**, so do the first run on a spare account in a private
channel and watch the dashboard.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Config problems:` | The list says which option and why |
| "official discord.py" message | `pip uninstall -y discord.py`, then `pip install -r requirements.txt`, or just use `start.bat` / `start.sh` |
| "Discord rejected the token" | Copy a fresh token; tokens change when the password changes |
| Sends messages but nothing is answered | Wrong channel ID, OwO is not in that channel, or the account has not opened the server recently. Large servers may need the channel opened once in Discord |
| Paused with `no_reply` | OwO is not answering: look in the channel for a captcha or a typo in `farm.prefix` |
| "The web dashboard could not start" | The port is taken: change `dashboard.port`, or close the other copy |
| Stats page says 403 | Open it as `http://127.0.0.1:PORT`, not under another name |
| No DM arrives | Check the log for "DM alert failed": the alert account must be your friend or share a server with you, and your DMs must be open. `--check` says whether its token was found |
| Garbled characters on Windows | Use Windows Terminal, or run with `--plain` |

## Files

| File | Purpose |
|---|---|
| `owo_farmer.py` | the whole program |
| `config.example.toml` | every option, documented (`--init` writes the same text to `config.toml`) |
| `test_owo_farmer.py` | the offline test suite |
| `start.bat`, `start.sh` | build the environment, install, run (and repair an interrupted setup) |
| `requirements.txt` | dependencies |
| `state.json`, `logs/` | created at run time (daily timer, gambling loss per day, rotating log; tokens are redacted) |

## Credits

Feature ideas and OwO's reply formats were studied in
[owo-dusk](https://github.com/owo-dusk/owo-dusk), [owomizu](https://github.com/Kiy0w0/owomizu) and
[neura-self](https://github.com/routo-loop/neura-self) (all GPL-3.0) and in OwO's public source. This is a
fresh implementation and no code was copied from them, including the gem handling, which reads the charge bars
with its own code. Unlike them it does not solve captchas, by design.
