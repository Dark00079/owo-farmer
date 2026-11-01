#!/usr/bin/env python3
"""
OwO Farmer - a careful OwO-bot grinder that runs as a Discord self-bot.

What it does
    * Sends owo / hunt / battle / pray / curse / daily / cookie / sell / sac / lootbox / crate,
      gem upgrades and your own custom commands, always on OwO's real cooldowns.
    * Optional coinflip / slots gambling: flat or martingale bets, hard caps, stop-loss,
      take-profit, daily loss limit and balance-aware sizing.
    * Reads OwO's replies: pauses on a captcha or ban notice, backs off on "slow down",
      learns the daily reset from OwO itself and notices when OwO stops answering.
    * Live terminal dashboard (needs `rich`), rotating log file, optional webhook alerts.

What it deliberately does NOT do
    * It never solves captchas. It stops, alerts you, and carries on once YOU have solved it.
    * It does not fake typing, send filler chat or otherwise pretend to be a person.

Self-bots break Discord's Terms of Service and OwO's rules. Nothing here makes that safe.
Use it on an account you can afford to lose.

Run `python owo_farmer.py --help` to get started.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import datetime as dt
import difflib
import heapq
import hmac
import itertools
import json
import logging
import logging.handlers
import os
import random
import re
import signal
import sys
import tempfile
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

__version__ = "1.0.0"

OWO_ID = 408785106942164992
OWO_MAX_BET = 250_000
HERE = Path(__file__).resolve().parent
Range = Tuple[float, float]

EXIT_OK, EXIT_CONFIG, EXIT_SAFETY, EXIT_LOGIN, EXIT_CHANNEL = 0, 1, 2, 3, 4

# OwO's own cooldowns (seconds) plus a small margin. A configured cooldown below these is raised,
# so a typo can never turn the script into a spammer.
MIN_COOLDOWN: Dict[str, float] = {
    "owo": 10.5, "hunt": 15.5, "battle": 15.5, "pray": 301.0, "cookie": 5.5, "sell": 1.5,
    "sac": 1.5, "lootbox": 5.5, "crate": 30.5, "cash": 5.5, "gamble": 15.5, "custom": 5.0,
}

# ======================================================================================
# 1. Configuration
# ======================================================================================

DEFAULTS: Dict[str, Any] = {
    "account": {"token_env": "DISCORD_TOKEN", "token": ""},
    "farm": {
        "channel_id": 0,
        "prefix": "owo",
        "short_commands": True,
        "silent": True,
        "gap": (3.5, 7.0),
        "startup_delay": (3.0, 10.0),
        "reply_timeout": 25.0,
        "max_missed_replies": 5,
        "max_messages_per_minute": 12,
        "sync_manual_commands": True,
        "extra_names": [],
        "trust_channel": False,
    },
    "commands": {
        "owo": {"enabled": True, "cooldown": (10.5, 14.0)},
        "hunt": {"enabled": True, "cooldown": (15.5, 16.2), "instant_chance": 0.35, "delay": (2.0, 3.0)},
        "battle": {"enabled": True, "cooldown": (15.5, 16.2), "instant_chance": 0.35, "delay": (2.0, 3.0)},
        "pray": {"enabled": True, "cooldown": (305.0, 345.0), "targets": [], "curse_targets": []},
        "daily": {"enabled": True},
        "cookie": {"enabled": False, "cooldown": (86400.0, 90000.0), "target": 0},
        "sell": {"enabled": False, "cooldown": (1500.0, 2100.0), "args": ""},
        "sac": {"enabled": False, "cooldown": (1500.0, 2100.0), "args": ""},
        "lootbox": {"enabled": False, "cooldown": (3600.0, 4200.0), "args": "all"},
        "crate": {"enabled": False, "cooldown": (3600.0, 4200.0), "args": "all"},
        "cash": {"enabled": True, "cooldown": (900.0, 1500.0)},
    },
    "gems": {
        "enabled": False,
        "types": ["hunting", "empowering", "lucky"],
        "tiers": ["common", "uncommon", "rare", "epic", "mythical", "legendary", "fabled"],
        "lowest_first": True,
        "check_cooldown": 90.0,
        "empty_retry_minutes": 45.0,
    },
    "gamble": {
        "enabled": False,
        "game": "coinflip",
        "side": "heads",
        "strategy": "flat",
        "base_bet": 100,
        "multiplier": 2.0,
        "max_streak": 5,
        "max_bet": 5000,
        "max_balance_fraction": 0.10,
        "keep_balance": 500,
        "stop_loss": 2000,
        "take_profit": 5000,
        "daily_loss_limit": 5000,
        "max_bets": 0,
        "cooldown": (16.0, 22.0),
        "broke_pause_minutes": 30.0,
    },
    "safety": {
        "captcha_resume_delay": (20.0, 60.0),
        "captcha_remind_seconds": 45.0,
        "captcha_give_up_minutes": 20.0,
        "slowdown_limit": 4,
        "slowdown_window_minutes": 10.0,
        "slowdown_pause": (120.0, 300.0),
        "quiet_hours": [],
        "max_runtime_minutes": 0.0,
        "max_commands": 0,
        "break_every_minutes": 10.0,
        "break_minutes": 3.0,
    },
    "alerts": {
        "bell": True,
        "sound": True,
        "webhook_url": "",
        "ping_user_id": 0,
        "dm": {
            "enabled": False,
            "token_env": "ALERT_ACCOUNT_TOKEN",
            "token": "",
            "user_id": 0,
            "interval_seconds": 30.0,
            "max_pings": 60,
        },
    },
    "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8787, "password": ""},
    "ui": {"terminal_dashboard": True},
    "logging": {"level": "INFO", "file": "logs/farmer.log"},
    "custom": [],
}

CONFIG_TEMPLATE = r'''# ======================================================================================
#  OwO Farmer - configuration            (copy of config.example.toml; edit config.toml)
#  Ranges are [min, max] in seconds; the script waits a random time inside the range.
#  Anything below OwO's real cooldown is raised automatically (you will see a warning).
# ======================================================================================

[account]
# Name of the environment variable that holds your Discord token. Keeping the token out of
# this file is the safest option (PowerShell:  $env:DISCORD_TOKEN = "your-token").
token_env = "DISCORD_TOKEN"
# Or paste it here. config.toml is git-ignored, but never share or upload this file with it filled in.
token = ""

[farm]
# REQUIRED. Right-click the channel > Copy Channel ID (turn on Developer Mode in Discord settings).
# A private channel that only you and OwO use works best.
channel_id = 0
prefix = "owo"              # "owo" or "w"
short_commands = true       # send "h" / "b" instead of "hunt" / "battle"
silent = true               # send as @silent messages (no notification for anyone in the channel)
gap = [3.5, 7.0]            # pause between ANY two messages we send
startup_delay = [3, 10]     # wait before the very first message
reply_timeout = 25.0        # how long to wait for OwO to answer a command
max_missed_replies = 5      # pause after this many unanswered commands in a row
max_messages_per_minute = 12   # hard ceiling, whatever else is configured
sync_manual_commands = true # if you type a command yourself, reset that command's timer
extra_names = []            # other names OwO may call you by (it writes your name in its replies). Rarely needed
trust_channel = false       # true = treat EVERY OwO message in this channel as yours (private channels only)

[commands.owo]              # the plain "owo" counter message
enabled = true
cooldown = [10.5, 14.0]

[commands.hunt]
enabled = true
cooldown = [15.5, 16.2]     # the "instant" case: right after OwO's own 15 s cooldown (never below 15.5)
instant_chance = 0.35       # how often the next hunt goes out right away
delay = [2.0, 3.0]          # the rest of the time it waits this many extra seconds first

[commands.battle]           # same timing rules as hunt
enabled = true
cooldown = [15.5, 16.2]
instant_chance = 0.35
delay = [2.0, 3.0]

[commands.pray]             # pray and curse share one cooldown in OwO, so they share one setting
enabled = true
cooldown = [305.0, 345.0]
targets = []                # user IDs to pray for (picked at random). Empty = pray for yourself
curse_targets = []          # user IDs to curse. If set, the script alternates pray / curse

[commands.daily]            # claimed once per day; the script learns the reset time from OwO
enabled = true

[commands.cookie]
enabled = false
cooldown = [86400.0, 90000.0]
target = 0                  # REQUIRED when enabled: user ID that gets your cookie

[commands.sell]             # exactly what you would type after "owo sell", e.g. "common uncommon"
enabled = false
cooldown = [1500.0, 2100.0]
args = ""

[commands.sac]              # exactly what you would type after "owo sac"
enabled = false
cooldown = [1500.0, 2100.0]
args = ""

[commands.lootbox]
enabled = false
cooldown = [3600.0, 4200.0]
args = "all"

[commands.crate]
enabled = false
cooldown = [3600.0, 4200.0]
args = "all"

[commands.cash]             # balance tracking (also needed for gambling)
enabled = true
cooldown = [900.0, 1500.0]

[gems]                      # keeps your gem types active by reading your hunts and inventory
enabled = false
types = ["hunting", "empowering", "lucky"]     # add "special" if you want it too
tiers = ["common", "uncommon", "rare", "epic", "mythical", "legendary", "fabled"]
lowest_first = true         # use the weakest allowed tier first
check_cooldown = 90.0       # minimum seconds between inventory checks
empty_retry_minutes = 45.0  # how long to wait before checking again when you have no gems

[gamble]                    # OFF by default. The house wins on average; the limits below protect you.
enabled = false
game = "coinflip"           # "coinflip" or "slots"
side = "heads"              # coinflip only: "heads", "tails" or "random"
strategy = "flat"           # "flat" = same bet every time, "martingale" = multiply after each loss
base_bet = 100
multiplier = 2.0            # martingale only
max_streak = 5              # martingale: after this many losses in a row, go back to base_bet (0 = never)
max_bet = 5000              # no bet is ever larger than this (OwO's own cap is 250,000)
max_balance_fraction = 0.10 # never bet more than this share of your known balance
keep_balance = 500          # always leave at least this much cowoncy untouched
stop_loss = 2000            # stop gambling once this much is lost this session (0 = off)
take_profit = 5000          # stop gambling once this much is won this session (0 = off)
daily_loss_limit = 5000     # across restarts, per calendar day (0 = off)
max_bets = 0                # bets per session (0 = unlimited)
cooldown = [16.0, 22.0]
broke_pause_minutes = 30.0  # wait this long when the balance is too low or OwO says you are broke

[safety]
captcha_resume_delay = [20, 60]    # wait after OwO says you are verified before carrying on
captcha_remind_seconds = 45.0      # repeat the alert this often until the captcha is solved
captcha_give_up_minutes = 20.0     # exit if it still is not solved (0 = wait forever)
slowdown_limit = 4                 # this many "slow down" warnings ...
slowdown_window_minutes = 10.0     # ... inside this window pauses everything for a while
slowdown_pause = [120, 300]
quiet_hours = []                   # e.g. ["01:00-07:30", "13:00-14:00"] (your local time)
max_runtime_minutes = 0.0          # stop after this long (0 = no limit)
max_commands = 0                   # stop after this many messages (0 = no limit)
break_every_minutes = 10.0         # after this much running time ...
break_minutes = 3.0                # ... rest for this long, then carry on (set both to 0 for no breaks)

[alerts]
bell = true                        # terminal bell on captcha / ban / pause
sound = true                       # louder beeps on Windows for captcha / ban
webhook_url = ""                   # optional Discord webhook for captcha / ban / stop alerts
ping_user_id = 0                   # user ID to @mention in webhook alerts (0 = nobody)

[alerts.dm]                        # on a captcha, DM you from a SECOND account and keep pinging until you reply "stop"
enabled = false
token_env = "ALERT_ACCOUNT_TOKEN"  # environment variable holding that second account's token
token = ""                         # or put it here (config.toml is git-ignored). Never paste it in chat or commit it
user_id = 0                        # REQUIRED when enabled: the user ID that receives the DMs (yours)
interval_seconds = 30.0            # time between pings (at least 15)
max_pings = 60                     # stop pinging after this many, even without a reply

[dashboard]                        # read-only stats page, served by this script while it runs
enabled = true
host = "127.0.0.1"                 # 127.0.0.1 = this computer only. "0.0.0.0" = reachable from other devices (needs a password)
port = 8787
password = ""                      # required (8+ characters) whenever host is not 127.0.0.1 / localhost

[ui]
terminal_dashboard = true          # live dashboard inside this terminal (plain log lines without rich, or with --plain)

[logging]
level = "INFO"                     # DEBUG, INFO, WARNING, ERROR
file = "logs/farmer.log"           # relative to this config file; "" disables the file log

# Your own timed commands. Copy this block as often as you like.
# [[custom]]
# text = "zoo"                     # sent after the prefix, e.g. "owo zoo"
# cooldown = [600, 900]
# enabled = true
# prefix = true                    # false = send the text exactly as written
# expects_reply = false            # true = OwO always answers it, so a missing answer counts as "OwO is silent"
'''


class ConfigError(Exception):
    def __init__(self, problems: List[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _coerce(value: Any, default: Any, path: str, errs: List[str]) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        errs.append(f"{path}: expected true or false, got {value!r}")
    elif isinstance(default, int):
        if _is_num(value) and float(value).is_integer():
            return int(value)
        errs.append(f"{path}: expected a whole number, got {value!r}")
    elif isinstance(default, float):
        if _is_num(value):
            return float(value)
        errs.append(f"{path}: expected a number, got {value!r}")
    elif isinstance(default, str):
        if isinstance(value, str):
            return value.strip()
        errs.append(f"{path}: expected text in quotes, got {value!r}")
    elif isinstance(default, tuple):
        if (isinstance(value, (list, tuple)) and len(value) == 2 and all(_is_num(x) for x in value)
                and 0 <= value[0] <= value[1]):
            return (float(value[0]), float(value[1]))
        errs.append(f"{path}: expected [min, max] with 0 <= min <= max, got {value!r}")
    elif isinstance(default, list):
        if isinstance(value, list):
            return list(value)
        errs.append(f"{path}: expected a list, got {value!r}")
    return default


def _merge(defaults: Dict[str, Any], data: Any, path: str, errs: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(data, dict):
        errs.append(f"{path or 'config'}: expected a [table]")
        data = {}
    for key in data:
        if key not in defaults:
            near = difflib.get_close_matches(str(key), list(defaults), n=1)
            hint = f" (did you mean '{near[0]}'?)" if near else ""
            errs.append(f"{path + '.' if path else ''}{key}: unknown option{hint}")
    for key, default in defaults.items():
        p = f"{path}.{key}" if path else key
        if isinstance(default, dict):
            out[key] = _merge(default, data.get(key, {}), p, errs)
        elif key in data:
            out[key] = _coerce(data[key], default, p, errs)
        else:
            out[key] = default
    return out


def parse_quiet(spec: str) -> Tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", spec or "")
    if not m:
        raise ValueError(spec)
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise ValueError(spec)
    return h1 * 60 + m1, h2 * 60 + m2


def in_quiet(minute_of_day: int, windows: List[Tuple[int, int]]) -> bool:
    for a, b in windows:
        if a == b:
            continue
        if (a < b and a <= minute_of_day < b) or (a > b and (minute_of_day >= a or minute_of_day < b)):
            return True
    return False


def _clamp_range(path: str, rng: Range, floor: float, warnings: List[str]) -> Range:
    lo, hi = rng
    if lo >= floor:
        return rng
    new_lo = floor
    new_hi = max(hi, new_lo + 0.5)
    warnings.append(f"{path}: {lo:g}-{hi:g}s is faster than OwO allows; using {new_lo:g}-{new_hi:g}s")
    return (new_lo, new_hi)


def _ns(obj: Any) -> Any:
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(v) for v in obj]
    return obj


def build_config(raw: Dict[str, Any], require_channel: bool = True) -> Tuple[SimpleNamespace, List[str]]:
    """Validate `raw` (parsed TOML) against DEFAULTS. Returns (config, warnings) or raises ConfigError."""
    errs: List[str] = []
    warns: List[str] = []
    cfg = _merge(DEFAULTS, raw, "", errs)
    farm, cmds, gem, gam, saf, alr = cfg["farm"], cfg["commands"], cfg["gems"], cfg["gamble"], cfg["safety"], cfg["alerts"]

    if require_channel and farm["channel_id"] <= 0 and not any(e.startswith("farm.channel_id") for e in errs):
        errs.append("farm.channel_id: set the ID of the channel you want to farm in")
    if not farm["prefix"] or " " in farm["prefix"]:
        errs.append("farm.prefix: must be a single word such as \"owo\" or \"w\"")
    farm["gap"] = _clamp_range("farm.gap", farm["gap"], 2.0, warns)
    if farm["max_messages_per_minute"] < 1:
        errs.append("farm.max_messages_per_minute: must be at least 1")
    if farm["max_messages_per_minute"] > 20:
        warns.append("farm.max_messages_per_minute: capped at 20")
        farm["max_messages_per_minute"] = 20
    if farm["max_missed_replies"] < 2:
        errs.append("farm.max_missed_replies: must be at least 2")
    if not all(isinstance(x, str) and x.strip() for x in farm["extra_names"]):
        errs.append("farm.extra_names: must be a list of names in quotes")

    for name in ("owo", "hunt", "battle", "pray", "cookie", "sell", "sac", "lootbox", "crate", "cash"):
        c = cmds[name]
        c["cooldown"] = _clamp_range(f"commands.{name}.cooldown", c["cooldown"], MIN_COOLDOWN[name], warns)
    pray = cmds["pray"]
    for key in ("targets", "curse_targets"):
        if not all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in pray[key]):
            errs.append(f"commands.pray.{key}: must be a list of user IDs (numbers)")
    for name in ("hunt", "battle"):
        if not 0 <= cmds[name]["instant_chance"] <= 1:
            errs.append(f"commands.{name}.instant_chance: must be between 0 and 1")
    if cmds["cookie"]["enabled"] and cmds["cookie"]["target"] <= 0:
        errs.append("commands.cookie.target: set the user ID that receives the cookie")
    for name in ("sell", "sac"):
        if cmds[name]["enabled"] and not cmds[name]["args"]:
            errs.append(f"commands.{name}.args: say what to {name}, e.g. \"common uncommon\" (it is sent after the prefix)")
    for name in ("lootbox", "crate"):
        if cmds[name]["enabled"] and not cmds[name]["args"]:
            cmds[name]["args"] = "all"

    tier_names = DEFAULTS["gems"]["tiers"]
    if not set(gem["types"]) <= set(GEM_BASE):
        errs.append(f"gems.types: choose from {', '.join(GEM_BASE)}")
    if not set(gem["tiers"]) <= set(tier_names):
        errs.append(f"gems.tiers: choose from {', '.join(tier_names)}")
    if gem["enabled"] and (not gem["types"] or not gem["tiers"]):
        errs.append("gems: types and tiers cannot be empty while gems are enabled")
    if gem["check_cooldown"] < 30:
        warns.append("gems.check_cooldown: raised to 30s")
        gem["check_cooldown"] = 30.0

    if gam["game"] not in ("coinflip", "slots"):
        errs.append("gamble.game: must be \"coinflip\" or \"slots\"")
    if gam["side"] not in ("heads", "tails", "random"):
        errs.append("gamble.side: must be \"heads\", \"tails\" or \"random\"")
    if gam["strategy"] not in ("flat", "martingale"):
        errs.append("gamble.strategy: must be \"flat\" or \"martingale\"")
    if gam["base_bet"] < 1:
        errs.append("gamble.base_bet: must be at least 1")
    if gam["max_bet"] < gam["base_bet"]:
        errs.append("gamble.max_bet: must be at least base_bet")
    if gam["strategy"] == "martingale" and gam["multiplier"] < 1.1:
        errs.append("gamble.multiplier: must be at least 1.1 for martingale")
    if not 0 < gam["max_balance_fraction"] <= 1:
        errs.append("gamble.max_balance_fraction: must be above 0 and at most 1")
    for key in ("keep_balance", "stop_loss", "take_profit", "daily_loss_limit", "max_bets", "max_streak"):
        if gam[key] < 0:
            errs.append(f"gamble.{key}: cannot be negative")
    gam["max_bet"] = min(gam["max_bet"], OWO_MAX_BET)
    gam["cooldown"] = _clamp_range("gamble.cooldown", gam["cooldown"], MIN_COOLDOWN["gamble"], warns)
    if gam["enabled"] and not cmds["cash"]["enabled"]:
        warns.append("commands.cash: switched on because gambling needs your balance")
        cmds["cash"]["enabled"] = True

    windows = []
    for spec in saf["quiet_hours"]:
        try:
            windows.append(parse_quiet(spec))
        except (ValueError, TypeError):
            errs.append(f"safety.quiet_hours: {spec!r} is not like \"01:00-07:30\"")
    saf["quiet_windows"] = windows
    if saf["slowdown_limit"] < 1:
        errs.append("safety.slowdown_limit: must be at least 1")
    if saf["captcha_remind_seconds"] < 10:
        saf["captcha_remind_seconds"] = 10.0
    every, rest = saf["break_every_minutes"], saf["break_minutes"]
    if bool(every) != bool(rest):
        errs.append("safety.break_every_minutes / break_minutes: set both, or set both to 0 for no breaks")
    elif every and every < 1:
        errs.append("safety.break_every_minutes: must be at least 1")

    dm = alr["dm"]
    if dm["enabled"] and dm["user_id"] <= 0:
        errs.append("alerts.dm.user_id: set the user ID that should receive the DMs")
    if dm["interval_seconds"] < 15:
        warns.append("alerts.dm.interval_seconds: raised to 15s")
        dm["interval_seconds"] = 15.0
    if dm["max_pings"] < 1:
        errs.append("alerts.dm.max_pings: must be at least 1")

    dash = cfg["dashboard"]
    if not dash["host"]:
        errs.append("dashboard.host: cannot be empty")
    if not 0 <= dash["port"] <= 65535:
        errs.append("dashboard.port: must be between 0 and 65535")
    if dash["enabled"] and dash["host"] not in ("127.0.0.1", "localhost", "::1") and len(dash["password"]) < 8:
        errs.append("dashboard.password: needed (8+ characters) because the dashboard is open to other devices")

    url = alr["webhook_url"]
    if url and not re.match(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$", url):
        errs.append("alerts.webhook_url: must be a Discord webhook URL (https://discord.com/api/webhooks/...)")

    if cfg["logging"]["level"].upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        errs.append("logging.level: must be DEBUG, INFO, WARNING or ERROR")
    cfg["logging"]["level"] = cfg["logging"]["level"].upper()

    custom: List[Dict[str, Any]] = []
    for i, item in enumerate(cfg["custom"]):
        p = f"custom[{i}]"
        merged = _merge({"text": "", "cooldown": (60.0, 90.0), "enabled": True, "prefix": True,
                         "expects_reply": False}, item, p, errs)
        if not merged["text"]:
            errs.append(f"{p}.text: required")
        merged["cooldown"] = _clamp_range(f"{p}.cooldown", merged["cooldown"], MIN_COOLDOWN["custom"], warns)
        custom.append(merged)
    cfg["custom"] = custom

    if errs:
        raise ConfigError(errs)
    return _ns(cfg), warns


def load_config(path: Path, require_channel: bool = True) -> Tuple[SimpleNamespace, List[str]]:
    if tomllib is None:
        raise ConfigError(["Python 3.10 needs the 'tomli' package: pip install tomli"])
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ConfigError([f"{path} not found. Run:  python owo_farmer.py --init"]) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"{path.name} is not valid TOML: {exc}"]) from None
    return build_config(raw, require_channel)


def resolve_token(cfg: SimpleNamespace) -> str:
    token = os.environ.get(cfg.account.token_env, "").strip() or cfg.account.token.strip()
    token = token.strip('"').strip("'")
    if not token:
        raise ConfigError([
            f"No token found. Set the {cfg.account.token_env} environment variable or account.token in the config.",
        ])
    if re.search(r"\s", token):
        raise ConfigError(["The token contains spaces or line breaks; copy it again."])
    return token


def resolve_alert_token(cfg: SimpleNamespace, main_token: str) -> str:
    """Token of the SECOND account that sends the DM alerts (alerts.dm)."""
    dm = cfg.alerts.dm
    token = (os.environ.get(dm.token_env, "") or dm.token).strip().strip('"').strip("'")
    if not token:
        raise ConfigError([f"alerts.dm is on but no token was found. Set the {dm.token_env} environment variable "
                           "(or alerts.dm.token) to the second account's token."])
    if re.search(r"\s", token):
        raise ConfigError(["The alert account's token contains spaces or line breaks; copy it again."])
    if token == main_token:
        raise ConfigError(["alerts.dm: the alert account must be a different account from the one that farms."])
    return token


# ======================================================================================
# 2. Reading OwO's messages
# ======================================================================================

_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
_WS = re.compile(r"\s+")
_TS = re.compile(r"<t:(\d+)(?::[a-zA-Z])?>")
_GEM_EMOJI = re.compile(r"<a?:[a-z]?(gem1|gem3|gem4|star):\d+>", re.I)
# The charge bar next to each gem in a hunt reply: <:cgem1:123>`[1/25]` means 1 of 25 uses left.
_GEM_STATUS = re.compile(r"<a?:[a-z]?(gem1|gem3|gem4|star):\d+>\s*`?\[(\d+)\s*/\s*(\d+)\]", re.I)
_ANIMAL = re.compile(r":[a-z0-9_+\-]+:|[\U0001F300-\U0001FAFF]")
_INVENTORY = re.compile(r"`(\d+)`.*?([⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
_SMALL_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_DURATION = re.compile(r"(\d+)\s*([dhms])\b")
GEM_TYPE_BY_EMOJI = {"gem1": "hunting", "gem3": "empowering", "gem4": "lucky", "star": "special"}

CAPTCHA_PHRASES = (
    "are you a real human", "complete your captcha", "verify that you are human", "captcha", "letterword",
)


def normalize(text: str) -> str:
    """Lower-case, fold fancy unicode letters, drop invisible characters and custom emoji."""
    text = unicodedata.normalize("NFKC", text or "").replace("’", "'")
    text = _CUSTOM_EMOJI.sub(" ", text)
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in ("Cf", "Cc"))
    return _WS.sub(" ", text).casefold().strip()


def squash(text: str) -> str:
    """Letters and digits only. Used to match your name inside OwO's bold-formatted replies."""
    return re.sub(r"[\W_]+", "", normalize(text))


def to_int(text: str) -> int:
    return int(re.sub(r"\D", "", text) or 0)


def parse_duration(text: str) -> Optional[float]:
    found, total = False, 0
    for num, unit in _DURATION.findall(text.lower()):
        found = True
        total += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return float(total) if found else None


@dataclass
class Facts:
    """Everything the farmer cares about in one OwO message."""
    ban: bool = False
    captcha: bool = False
    verified: bool = False
    cooldown: bool = False
    cooldown_until: Optional[float] = None
    throttled: bool = False
    no_cowoncy: bool = False
    cash: Optional[int] = None
    daily_ok: Optional[int] = None
    wait: Optional[float] = None
    hunt: bool = False
    gems_active: frozenset = frozenset()
    gem_levels: Dict[str, Tuple[int, int]] = field(default_factory=dict)   # type -> (uses left, uses total)
    xp: int = 0
    animals: int = 0
    battle: bool = False
    pray: bool = False
    curse: bool = False
    cf: Optional[Tuple[int, int]] = None       # (bet, payout); payout 0 means lost
    slots: Optional[Tuple[int, int]] = None
    inventory: Dict[str, int] = field(default_factory=dict)
    gem_used: bool = False
    gem_refused: bool = False
    sold: Optional[int] = None


def parse_facts(raw: str, *, verify_button: bool = False, warn_attachment: bool = False) -> Facts:
    f = Facts()
    n = normalize(raw)
    if "you have been banned" in n:
        f.ban = True
        return f
    if "i have verified that you are human" in n:
        f.verified = True
        return f
    if verify_button or warn_attachment or any(p in n for p in CAPTCHA_PHRASES):
        f.captcha = True
        return f
    if not n:
        return f

    if "slow down and try the command again" in n:
        f.cooldown = True
        m = _TS.search(raw)
        if m:
            f.cooldown_until = float(m.group(1))
    if "slow down~" in n or "too fast for me" in n:
        f.throttled = True
    if "don't have enough cowoncy" in n or "do not have enough cowoncy" in n:
        f.no_cowoncy = True

    m = re.search(r"you currently have[^\d]*([\d,]+)\s*[_*\s]*cowoncy", n)
    if m:
        f.cash = to_int(m.group(1))
    if "here is your daily" in n:
        m = re.search(r"here is your daily[^\d]*([\d,]+)", n)
        f.daily_ok = to_int(m.group(1)) if m else 0
    if "nu!" in n and "you need to wait" in n:
        f.wait = parse_duration(n.split("you need to wait", 1)[1])

    if "hunt is empowered by" in n or ("spent" in n and "and caught" in n):
        f.hunt = True
        named = {GEM_TYPE_BY_EMOJI[g.lower()] for g in _GEM_EMOJI.findall(raw)}
        for gem, left, total in _GEM_STATUS.findall(raw):
            f.gem_levels[GEM_TYPE_BY_EMOJI[gem.lower()]] = (int(left), int(total))
        # A bar reading [0/N] is used up: OwO shows it one last time, then drops it. Count it as gone now,
        # so the replacement is used before the next hunt instead of one hunt late.
        f.gems_active = frozenset(t for t in named if f.gem_levels.get(t, (1, 1))[0] > 0)
        xp = re.search(r"gained\D*(\d[\d,]*)\s*xp", n)
        f.xp = to_int(xp.group(1)) if xp else 0
        found = re.search(r"you found:([^\n]*)", n)
        f.animals = len(_ANIMAL.findall(found.group(1))) if found else 1
    if "goes into battle" in n:
        f.battle = True
    if "prays" in n:
        f.pray = True
    if "puts a curse on" in n or "is now cursed" in n:
        f.curse = True

    if "spent" in n and "chose" in n:
        bet = re.search(r"spent\D*([\d,]+)", n)
        won = re.search(r"and you won\D*([\d,]+)", n)
        if bet and (won or "and you lost it all" in n):
            f.cf = (to_int(bet.group(1)), to_int(won.group(1)) if won else 0)
    if "slots" in n and re.search(r"\bbet\b", n):
        bet = re.search(r"\bbet\D*([\d,]+)", n)
        if bet and "and won nothing" in n:
            f.slots = (to_int(bet.group(1)), 0)
        elif bet:
            won = re.search(r"and won\D*([\d,]+)", n)
            if won:
                f.slots = (to_int(bet.group(1)), to_int(won.group(1)))

    items = _INVENTORY.findall(raw)
    if items:
        f.inventory = {gid: int(small.translate(_SMALL_DIGITS)) for gid, small in items}
    if "you activated" in n:
        f.gem_used = True
    if "already have an active" in n or "do not own this gem" in n:
        f.gem_refused = True
    if "sold" in n:
        m = re.search(r"for a total of\D*([\d,]+)", n)
        if m:
            f.sold = to_int(m.group(1))
    return f


# ======================================================================================
# 3. Small building blocks: stats, state file, alerts, errors
# ======================================================================================

def fmt_dur(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def pacific_now(now: float) -> dt.datetime:
    try:
        from zoneinfo import ZoneInfo
        tz: dt.tzinfo = ZoneInfo("America/Los_Angeles")
    except Exception:  # no tz database (plain Windows without the tzdata package)
        tz = dt.timezone(dt.timedelta(hours=-8))
    return dt.datetime.fromtimestamp(now, tz)


def seconds_until_reset(now: float) -> float:
    """Seconds until the next midnight in Pacific time, which is when OwO's daily resets."""
    local = pacific_now(now)
    nxt = (local + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (nxt - local).total_seconds())


@dataclass
class Stats:
    started: float = 0.0
    sent: int = 0
    replies: int = 0
    missed: int = 0
    hunts: int = 0
    battles: int = 0
    prays: int = 0
    curses: int = 0
    dailies: int = 0
    daily_cowoncy: int = 0
    sold_cowoncy: int = 0
    captchas: int = 0
    slowdowns: int = 0
    manual: int = 0
    errors: int = 0
    cash: Optional[int] = None
    cash_start: Optional[int] = None
    xp: int = 0
    animals: int = 0
    breaks: int = 0


@dataclass
class Event:
    t: float
    level: str
    text: str


class StateFile:
    """Small JSON file that survives restarts (daily timer, gambling loss per day, lifetime totals)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.data: Dict[str, Any] = {"version": 1, "daily_next": 0.0, "gamble_date": "", "gamble_net": 0, "lifetime": {}}
        self.dirty = False
        self._last_save = 0.0
        if path and path.exists():
            with contextlib.suppress(OSError, ValueError):
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)

    def set(self, **kw: Any) -> None:
        self.data.update(kw)
        self.dirty = True

    def add_lifetime(self, stats: Stats) -> None:
        life = self.data.setdefault("lifetime", {})
        for key in ("sent", "hunts", "battles", "prays", "curses", "dailies", "daily_cowoncy", "captchas", "slowdowns",
                    "xp", "animals", "breaks"):
            life[key] = int(life.get(key, 0)) + int(getattr(stats, key))
        self.dirty = True

    def save(self, now: float = 0.0, force: bool = False) -> None:
        if not self.path or not (self.dirty or force):
            return
        if not force and now - self._last_save < 30:
            return
        self._last_save = now
        self.dirty = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass


class SendError(Exception):
    """Raised by a transport. kind: ratelimit | forbidden | captcha_required | network | http."""

    def __init__(self, kind: str, message: str = "", retry_after: float = 5.0):
        super().__init__(message or kind)
        self.kind = kind
        self.retry_after = retry_after


class NullAlerts:
    def __init__(self) -> None:
        self.sent: List[Tuple[str, str, str]] = []
        self.pinger: Any = None

    def notify(self, kind: str, title: str, body: str = "", urgent: bool = False) -> None:
        self.sent.append((kind, title, body))

    async def drain(self, timeout: float = 15.0) -> None:
        return None


class DmPinger:
    """DMs you from a SECOND account when something needs you, and keeps pinging until you answer "stop".

    The farming account sees your "stop" itself, because it is a party to that DM, so the alert account
    never needs a gateway connection: it only logs in over HTTP to send.
    """

    def __init__(self, cfg: SimpleNamespace, token: str, log: Callable[[str, str], None],
                 sender: Optional[Callable[[str], Awaitable[int]]] = None):
        self.cfg, self.token, self.log = cfg, token, log
        self._send = sender or self._discord_send
        self.channel_id = 0              # the DM channel; known once the first message went out
        self.active = False              # a ping series is running
        self.silenced = False            # you said stop (or the pings ran out): no new series for this alert
        self.dead = False                # unusable: bad token, DMs blocked. Stop trying
        self.pings = 0
        self.failures = 0
        self._series: Optional[asyncio.Task] = None
        self._once: List[asyncio.Task] = []
        self._client: Any = None
        self._dm: Any = None

    # ---- what the farmer calls -----------------------------------------------------

    def alert(self, kind: str, title: str, body: str) -> None:
        if kind == "captcha":
            self.start(f"{title} {body}".strip())
        elif kind in ("stop", "no_reply"):
            self._send_once(f"{title}: {body}")
        elif kind == "verified":
            self.clear()

    def wants_stop(self, m: Incoming) -> bool:
        """Is this message you typing 'stop' in the alert DM?"""
        return bool(m.from_me and m.is_dm and self.channel_id and m.channel_id == self.channel_id
                    and re.match(r"stop\b", normalize(m.text)))

    def start(self, text: str) -> None:
        if self.active or self.silenced or self.dead:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.active, self.pings = True, 0
        self._series = loop.create_task(self._repeat(text))
        self.log("info", "DM alert: pinging you until you reply 'stop'")

    def stop(self, why: str) -> None:
        """You said stop. No more pings until the next captcha has been dealt with."""
        was = self.active
        self.active = False
        self.silenced = True
        if self._series and not self._series.done():
            self._series.cancel()
        if was:
            self.log("info", f"DM alerts stopped ({why})")

    def clear(self) -> None:
        """The captcha is over: end any pings and allow a fresh series for the next one."""
        was = self.active
        self.active = False
        if self._series and not self._series.done():
            self._series.cancel()
        if was:
            self.log("info", "DM alerts stopped (captcha solved)")
        self.silenced = False

    async def drain(self, timeout: float = 15.0) -> None:
        pending = [t for t in self._once if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    async def close(self) -> None:
        self.active = False
        if self._series and not self._series.done():
            self._series.cancel()
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.close()
            self._client = None

    # ---- internals -----------------------------------------------------------------

    async def _repeat(self, text: str) -> None:
        try:
            while self.active and not self.dead and self.pings < self.cfg.max_pings:
                n = self.pings + 1
                msg = (f"<@{self.cfg.user_id}> ⚠️ {text}\n"
                       f"Reply **stop** in this chat to stop these pings. ({n}/{self.cfg.max_pings})")
                if await self._deliver(msg):
                    self.pings = n
                if self.pings >= self.cfg.max_pings:
                    break
                await asyncio.sleep(self.cfg.interval_seconds)
            if self.active and self.pings >= self.cfg.max_pings:
                self.silenced = True
                self.log("info", "DM alert: reached the maximum number of pings")
        finally:
            self.active = False

    def _send_once(self, text: str) -> None:
        if self.dead:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._once = [t for t in self._once if not t.done()]
        self._once.append(loop.create_task(self._deliver(f"<@{self.cfg.user_id}> {text}")))

    async def _deliver(self, text: str) -> bool:
        try:
            channel_id = await self._send(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            self.failures += 1
            self.log("warn", f"DM alert failed ({name})")
            hard = name in ("LoginFailure", "CaptchaRequired")
            if hard or self.failures >= 3:
                self.dead, self.active = True, False
                self.log("error", "DM alerts switched off: " + (
                    "the alert account was rejected or Discord wants a captcha for it" if hard else
                    "three sends in a row failed (the alert account must be your friend or share a server with you)"))
            return False
        if channel_id:
            self.channel_id = int(channel_id)
        self.failures = 0
        return True

    async def _discord_send(self, text: str) -> int:
        import discord
        if self._client is None:
            client = discord.Client()
            await client.login(self.token)
            self._client = client
        if self._dm is None:
            self._dm = await self._client.create_dm(discord.Object(id=self.cfg.user_id))
        await self._dm.send(text)
        return int(self._dm.id)


class Alerts:
    """Terminal bell, Windows beeps, an optional Discord webhook and optional DMs from a second account."""

    def __init__(self, cfg: SimpleNamespace, log: Callable[[str, str], None], pinger: Optional[DmPinger] = None):
        self.cfg, self.log, self.pinger = cfg, log, pinger
        self._tasks: List[asyncio.Task] = []

    async def drain(self, timeout: float = 15.0) -> None:
        """Let alerts that are still on their way (webhook, one-off DMs) finish before the program ends."""
        pending = [t for t in self._tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)
        if self.pinger:
            await self.pinger.drain(timeout)

    def notify(self, kind: str, title: str, body: str = "", urgent: bool = False) -> None:
        if self.pinger:
            self.pinger.alert(kind, title, body)
        if kind == "verified":
            return
        if self.cfg.bell:
            with contextlib.suppress(Exception):
                sys.stdout.write("\a")
                sys.stdout.flush()
        if self.cfg.sound and urgent and os.name == "nt":
            threading.Thread(target=self._beep, daemon=True).start()
        if self.cfg.webhook_url:
            try:
                self._tasks.append(asyncio.get_running_loop().create_task(self._webhook(title, body)))
            except RuntimeError:
                pass

    @staticmethod
    def _beep() -> None:
        with contextlib.suppress(Exception):
            import winsound
            for _ in range(3):
                winsound.Beep(1000, 250)
                time.sleep(0.1)

    async def _webhook(self, title: str, body: str) -> None:
        try:
            import aiohttp
            mention = f"<@{self.cfg.ping_user_id}> " if self.cfg.ping_user_id else ""
            payload = {
                "content": f"{mention}**{title}**\n{body}"[:1900],
                "allowed_mentions": {"users": [str(self.cfg.ping_user_id)] if self.cfg.ping_user_id else []},
            }
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.cfg.webhook_url, json=payload) as resp:
                    if resp.status >= 300:
                        self.log("warn", f"webhook answered {resp.status}")
        except Exception as exc:  # alerts must never break the farm
            self.log("warn", f"webhook failed: {type(exc).__name__}")


# ======================================================================================
# 4. Gems and gambling (self-contained, unit-tested logic)
# ======================================================================================

GEM_BASE = {"hunting": 51, "empowering": 65, "lucky": 72, "special": 79}
TIERS = DEFAULTS["gems"]["tiers"]


def gem_id(kind: str, tier: str) -> str:
    return f"{GEM_BASE[kind] + TIERS.index(tier):03d}"


class GemManager:
    """Notices missing gem types from hunt replies, reads the inventory, then uses the best match."""

    def __init__(self, cfg: SimpleNamespace):
        self.cfg = cfg
        self.state = "idle"            # idle | inventory | use
        self.missing: List[str] = []
        self.use_ids: List[str] = []
        self.last_check = -1e9
        self.empty_until = 0.0
        self.attempts = 0
        self.stage_at = 0.0
        self.note = ""

    def on_hunt(self, active: frozenset, now: float) -> bool:
        """Returns True when an inventory check should start."""
        wanted = [t for t in self.cfg.types if t in GEM_BASE]
        missing = [t for t in wanted if t not in active]
        if not missing:
            self.attempts = 0
            return False
        backoff = self.cfg.check_cooldown * (2 ** min(self.attempts, 5))
        if self.state != "idle" or now < self.empty_until or now - self.last_check < backoff:
            return False
        self.missing, self.state, self.last_check, self.stage_at = missing, "inventory", now, now
        self.attempts += 1
        return True

    def choose(self, inventory: Dict[str, int]) -> List[str]:
        order = [t for t in self.cfg.tiers if t in TIERS]
        order.sort(key=TIERS.index, reverse=not self.cfg.lowest_first)
        ids = []
        for kind in self.missing:
            for tier in order:
                gid = gem_id(kind, tier)
                if inventory.get(gid, 0) > 0:
                    ids.append(gid)
                    break
        return ids

    def on_inventory(self, inventory: Dict[str, int], now: float) -> bool:
        if self.state != "inventory":
            return False
        self.use_ids = self.choose(inventory)
        if not self.use_ids:
            self.state = "idle"
            self.empty_until = now + self.cfg.empty_retry_minutes * 60
            self.note = f"no usable {'/'.join(self.missing)} gems, checking again later"
            return False
        self.state, self.stage_at = "use", now
        return True

    def use_text(self) -> str:
        return "use " + " ".join(str(int(g)) for g in self.use_ids)

    def done(self) -> None:
        self.state = "idle"
        self.use_ids = []

    def expire(self, now: float) -> None:
        if self.state != "idle" and now - self.stage_at > 60:
            self.done()


@dataclass
class Bet:
    amount: int
    at: float


class Gambler:
    """Bet sizing, win/loss accounting and every safety limit. No I/O, fully testable."""

    def __init__(self, cfg: SimpleNamespace, rng: random.Random, state: StateFile):
        self.cfg, self.rng, self.state = cfg, rng, state
        self.streak = 0
        self.net = 0
        self.bets = self.wins = self.losses = self.pushes = 0
        self.pending: Optional[Bet] = None
        self.halt_reason = ""
        self.resume_at: Optional[float] = None
        self.manual_off = False
        self.current_bet = cfg.base_bet

    def net_today(self, now: float) -> int:
        key = dt.date.fromtimestamp(now).isoformat()
        if self.state.data.get("gamble_date") != key:
            self.state.set(gamble_date=key, gamble_net=0)
        return int(self.state.data.get("gamble_net", 0))

    def halt(self, reason: str, now: float, minutes: Optional[float] = None) -> None:
        self.halt_reason = reason
        self.resume_at = now + minutes * 60 if minutes else None

    def active(self, now: float) -> bool:
        if self.resume_at is not None and now >= self.resume_at:
            self.halt_reason, self.resume_at = "", None
        return not self.halt_reason and not self.manual_off

    def status(self, now: float) -> str:
        if self.manual_off:
            return "switched off (g)"
        if self.halt_reason:
            return f"halted: {self.halt_reason}" + (f" ({fmt_dur(self.resume_at - now)})" if self.resume_at else "")
        return "betting" if not self.pending else "waiting for result"

    def next_bet(self, cash: Optional[int], now: float) -> Optional[int]:
        c = self.cfg
        if not self.active(now) or self.pending:
            return None
        if c.max_bets and self.bets >= c.max_bets:
            self.halt("max bets reached", now)
            return None
        if c.stop_loss and self.net <= -c.stop_loss:
            self.halt("stop-loss reached", now)
            return None
        if c.take_profit and self.net >= c.take_profit:
            self.halt("take-profit reached", now)
            return None
        today = self.net_today(now)
        if c.daily_loss_limit and today <= -c.daily_loss_limit:
            self.halt("daily loss limit reached", now)
            return None

        bet = float(c.base_bet)
        if c.strategy == "martingale":
            if c.max_streak and self.streak >= c.max_streak:
                self.streak = 0
            bet = c.base_bet * (c.multiplier ** self.streak)
        bet = min(bet, c.max_bet, OWO_MAX_BET)
        if c.stop_loss:
            bet = min(bet, c.stop_loss + self.net)
        if c.daily_loss_limit:
            bet = min(bet, c.daily_loss_limit + today)
        if cash is not None:
            bet = min(bet, cash * c.max_balance_fraction, cash - c.keep_balance)
        bet = int(bet)
        if bet < 1:
            self.halt("balance too low", now, c.broke_pause_minutes)
            return None
        self.current_bet = bet
        return bet

    def on_sent(self, bet: int, now: float) -> None:
        self.pending = Bet(bet, now)

    def on_result(self, bet: int, payout: int, now: float) -> Optional[int]:
        """Returns the net change, or None when this result is not for our pending bet."""
        if not self.pending or self.pending.amount != bet:
            return None
        self.pending = None
        delta = payout - bet
        self.net += delta
        self.state.set(gamble_net=self.net_today(now) + delta)
        self.bets += 1
        if delta > 0:
            self.wins += 1
            self.streak = 0
        elif delta < 0:
            self.losses += 1
            self.streak += 1
        else:
            self.pushes += 1
        return delta

    def expire(self, now: float, timeout: float = 45.0) -> bool:
        if self.pending and now - self.pending.at > timeout:
            self.pending = None
            return True
        return False


# ======================================================================================
# 5. The farmer
# ======================================================================================

@dataclass
class Incoming:
    """A message as the farmer sees it, independent of the Discord library."""
    id: int
    channel_id: int
    author_id: int
    text: str
    is_dm: bool = False
    from_me: bool = False
    mentions_me: bool = False
    refs_me: bool = False
    edited: bool = False
    verify_button: bool = False
    warn_attachment: bool = False
    url: str = ""


@dataclass
class Job:
    id: str
    label: str
    cooldown: Range
    priority: int
    build: Callable[[], Optional[str]]
    raw: bool = False
    expects_reply: bool = True
    ready: Optional[Callable[[], bool]] = None
    on_sent: Optional[Callable[[float], None]] = None
    after_send: Optional[Callable[[float], float]] = None     # custom "next due" after sending
    after_manual: Optional[Callable[[float], float]] = None   # custom "next due" after you typed it
    next_due: float = 0.0
    sent: int = 0
    last_sent: float = 0.0
    note: str = ""


@dataclass
class Pause:
    reason: str
    since: float
    until: Optional[float]
    note: str = ""


@dataclass
class Awaiting:
    job: str
    at: float


ALIASES = {
    "h": "hunt", "hunt": "hunt", "catch": "hunt", "b": "battle", "battle": "battle", "fight": "battle",
    "pray": "pray", "curse": "pray", "daily": "daily", "cookie": "cookie", "rep": "cookie", "sell": "sell",
    "sac": "sac", "sacrifice": "sac", "essence": "sac", "lb": "lootbox", "lootbox": "lootbox", "wc": "crate",
    "crate": "crate", "weaponcrate": "crate", "cash": "cash", "money": "cash", "balance": "cash",
    "owo": "owo", "uwu": "owo",
}


class Farmer:
    def __init__(self, cfg: SimpleNamespace, transport: Any, *, clock: Callable[[], float] = time.time,
                 rng: Optional[random.Random] = None, state: Optional[StateFile] = None,
                 alerts: Any = None, logger: Optional[logging.Logger] = None):
        self.cfg = cfg
        self.transport = transport
        self.clock = clock
        self.rng = rng or random.Random()
        self.state = state or StateFile()
        self.alerts = alerts or NullAlerts()
        self.logger = logger
        self.channel_id = cfg.farm.channel_id
        self.prefix = cfg.farm.prefix
        self.me_id = 0
        self.names: List[str] = []
        self.trust_channel = cfg.farm.trust_channel

        self.stats = Stats()
        self.events: Deque[Event] = deque(maxlen=300)
        self.notable: Deque[Event] = deque(maxlen=200)
        self.pauses: Dict[str, Pause] = {}
        self.stopped = False
        self.stop_reason = ""
        self.exit_code = EXIT_OK
        self.on_finish: Optional[Callable[[], None]] = None

        self.awaiting: Deque[Awaiting] = deque()
        self.missed = 0
        self.no_reply_pauses = 0
        self.send_errors = 0
        self.next_send_at = 0.0
        self.hold_until = 0.0
        self.boost = 1.0
        self.boost_until = 0.0
        self.sent_times: Deque[float] = deque()
        self.slow_events: Deque[float] = deque()
        self.started_at = 0.0
        self.captcha_since = 0.0
        self.captcha_alert_at = 0.0
        self.captcha_resume_at: Optional[float] = None
        self._recent_own: Dict[str, float] = {}
        self._resolved: Deque[int] = deque(maxlen=200)
        self._pray_turn = 0
        self._planned_bet = 0
        self.last_reply_at = 0.0
        self.last_job: Tuple[str, float] = ("", 0.0)
        self.gem_levels: Dict[str, Tuple[int, int]] = {}    # gem type -> (uses left, uses total), from the last hunt
        self.web_url = ""
        self._work = 0.0                                    # running seconds since the last break
        self._last_tick = 0.0

        self.gambler =Gambler(cfg.gamble, self.rng, self.state) if cfg.gamble.enabled else None
        self.gems = GemManager(cfg.gems) if cfg.gems.enabled else None
        self.jobs: Dict[str, Job] = {}
        self._build_jobs()

    # ---- logging / lifecycle -------------------------------------------------------

    def log(self, level: str, text: str) -> None:
        event = Event(self.clock(), level, text)
        self.events.append(event)
        if not text.startswith("sent: "):
            self.notable.append(event)             # kept apart so a stream of sent commands never pushes it out
        if self.logger:
            self.logger.log({"debug": 10, "info": 20, "warn": 30, "error": 40}.get(level, 20), text)

    @property
    def status(self) -> str:
        return "stopped" if self.stopped else "paused" if self.pauses else "running"

    def set_identity(self, me_id: int, names: List[str]) -> None:
        self.me_id = me_id
        everything = list(names) + list(self.cfg.farm.extra_names)
        self.names = sorted({squash(n) for n in everything if n and len(squash(n)) >= 2}, key=len, reverse=True)
        if not self.names and not self.trust_channel:
            self.trust_channel = True
            self.log("warn", "none of this account's names can be matched in OwO's replies, so every OwO message in "
                             "the channel is treated as yours. Use a private channel, or set farm.extra_names")

    def start(self) -> None:
        now = self.clock()
        self.started_at = self.stats.started = self._last_tick = now
        t0 = now + self.rng.uniform(*self.cfg.farm.startup_delay)
        self.next_send_at = t0
        slow_starters = {"cookie", "sell", "sac", "lootbox", "crate"}
        for job in self.jobs.values():
            job.next_due = t0 + self.rng.uniform(0, 4)
            if job.id in slow_starters or job.id.startswith("custom"):
                job.next_due = t0 + self.rng.uniform(60, 600)
        daily = self.jobs.get("daily")
        saved = float(self.state.data.get("daily_next") or 0)
        if daily and saved > now:
            daily.next_due = saved
        enabled = ", ".join(j.id for j in self.jobs.values())
        self.log("info", f"farming in channel {self.channel_id}: {enabled}")

    def request_stop(self, reason: str, code: int = EXIT_OK) -> None:
        self.stop(reason, code)

    def stop(self, reason: str, code: int = EXIT_OK, urgent: bool = False) -> None:
        if self.stopped:
            return
        self.stopped, self.stop_reason, self.exit_code = True, reason, code
        self.log("error" if code else "info", f"stopped: {reason}")
        if code:
            self.alerts.notify("stop", "OwO Farmer stopped", reason, urgent=urgent)
        self.state.add_lifetime(self.stats)
        self.state.save(self.clock(), force=True)
        if self.on_finish:
            self.on_finish()

    def pause(self, reason: str, seconds: Optional[float] = None, note: str = "", level: str = "warn") -> None:
        now = self.clock()
        until = now + seconds if seconds else None
        if reason in self.pauses:
            old = self.pauses[reason]
            old.until = until if until is None or old.until is None else max(old.until, until)
            old.note = note or old.note
            return
        self.pauses[reason] = Pause(reason, now, until, note)
        self.log(level, f"paused ({reason}){': ' + note if note else ''}")

    def resume(self, reason: str) -> None:
        if self.pauses.pop(reason, None) is None:
            return
        self.awaiting.clear()
        self.missed = 0
        self.next_send_at = max(self.next_send_at, self.clock() + self.rng.uniform(2.0, 5.0))
        self.log("info", f"resumed ({reason})")

    def toggle_manual_pause(self) -> None:
        if "manual" in self.pauses:
            self.resume("manual")
        else:
            self.pause("manual", note="paused with the p key")

    def captcha_solved(self) -> None:
        """Called by the c key: you solved it, carry on now."""
        self.captcha_resume_at = None
        self.resume("captcha")
        self.alerts.notify("verified", "Captcha solved", "")

    # ---- jobs ----------------------------------------------------------------------

    def _build_jobs(self) -> None:
        cfg, c, rng = self.cfg, self.cfg.commands, self.rng
        short = cfg.farm.short_commands

        def add(job: Job) -> None:
            self.jobs[job.id] = job

        def varied(cmd: SimpleNamespace) -> Callable[[float], float]:
            """Next due time: the cooldown, then either no extra wait ("instant") or a short random delay."""
            def when(now: float) -> float:
                extra = 0.0 if rng.random() < cmd.instant_chance else rng.uniform(*cmd.delay)
                return now + rng.uniform(*cmd.cooldown) + extra
            return when

        if c.owo.enabled:
            add(Job("owo", "owo", c.owo.cooldown, 8, lambda: "owo", raw=True, expects_reply=False))
        if c.hunt.enabled:
            add(Job("hunt", "hunt", c.hunt.cooldown, 5, lambda: "h" if short else "hunt",
                    after_send=varied(c.hunt), after_manual=varied(c.hunt)))
        if c.battle.enabled:
            add(Job("battle", "battle", c.battle.cooldown, 6, lambda: "b" if short else "battle",
                    after_send=varied(c.battle), after_manual=varied(c.battle)))
        if c.pray.enabled:
            add(Job("pray", "pray / curse", c.pray.cooldown, 4, self._build_pray))
        if c.daily.enabled:
            add(Job("daily", "daily", (60.0, 120.0), 3, lambda: "daily",
                    after_send=lambda now: now + 180,
                    after_manual=lambda now: now + seconds_until_reset(now) + rng.uniform(30, 300)))
        if c.cookie.enabled:
            add(Job("cookie", "cookie", c.cookie.cooldown, 9, lambda: f"cookie <@{c.cookie.target}>"))
        if c.sell.enabled:
            add(Job("sell", "sell", c.sell.cooldown, 9, lambda: f"sell {c.sell.args}"))
        if c.sac.enabled:
            add(Job("sac", "sacrifice", c.sac.cooldown, 9, lambda: f"sac {c.sac.args}"))
        if c.lootbox.enabled:
            add(Job("lootbox", "lootbox", c.lootbox.cooldown, 9, lambda: f"lb {c.lootbox.args}"))
        if c.crate.enabled:
            add(Job("crate", "weapon crate", c.crate.cooldown, 9, lambda: f"wc {c.crate.args}"))
        if c.cash.enabled:
            add(Job("cash", "balance", c.cash.cooldown, 2, lambda: "cash"))
        if self.gems:
            gems = self.gems
            add(Job("inv", "inventory", (3600.0, 3600.0), 1, lambda: "inv",
                    ready=lambda: gems.state == "inventory", after_send=lambda now: now + 3600))
            add(Job("use", "use gems", (3600.0, 3600.0), 1, gems.use_text,
                    ready=lambda: gems.state == "use", after_send=lambda now: now + 3600))
        if self.gambler:
            add(Job("gamble", f"gamble ({cfg.gamble.game})", cfg.gamble.cooldown, 7, self._build_gamble,
                    ready=lambda: self.stats.cash is not None and self.gambler is not None
                    and self.gambler.pending is None,
                    on_sent=self._gamble_sent))
        for i, item in enumerate(cfg.custom):
            if item.enabled:
                add(Job(f"custom{i}", item.text, item.cooldown, 10 + i, (lambda t=item.text: t), raw=not item.prefix,
                        expects_reply=item.expects_reply))

    def _build_pray(self) -> str:
        pray = self.cfg.commands.pray
        actions: List[Tuple[str, Optional[int]]] = [("pray", self.rng.choice(pray.targets) if pray.targets else None)]
        if pray.curse_targets:
            actions.append(("curse", self.rng.choice(pray.curse_targets)))
        self._pray_turn = (self._pray_turn + 1) % len(actions)
        verb, target = actions[self._pray_turn]
        return f"{verb} <@{target}>" if target else verb

    def _build_gamble(self) -> Optional[str]:
        g = self.gambler
        now = self.clock()
        if g is None:
            return None
        bet = g.next_bet(self.stats.cash, now)
        if bet is None:
            return None
        self._planned_bet = bet
        if self.cfg.gamble.game == "slots":
            return f"slots {bet}"
        side = self.cfg.gamble.side
        letter = self.rng.choice("ht") if side == "random" else side[0]
        return f"cf {bet} {letter}"

    def _gamble_sent(self, now: float) -> None:
        if self.gambler:
            self.gambler.on_sent(self._planned_bet, now)
            if self.gambler.bets and self.gambler.bets % 8 == 0 and "cash" in self.jobs:
                self.jobs["cash"].next_due = now + 6

    # ---- the clock tick ------------------------------------------------------------

    @property
    def gap(self) -> float:
        g = self.rng.uniform(*self.cfg.farm.gap)
        return g * (self.boost if self.clock() < self.boost_until else 1.0)

    async def tick(self) -> None:
        if self.stopped:
            return
        now = self.clock()
        self._expire_pauses(now)
        self._check_quiet(now)
        self._check_limits(now)
        self._check_break(now)
        self._watchdog(now)
        self._captcha_housekeeping(now)
        if self.gems:
            self.gems.expire(now)
        if self.gambler and self.gambler.expire(now):
            self.log("warn", "lost track of a bet (no result from OwO); continuing")
        self.state.save(now)
        if self.stopped or self.pauses or now < self.hold_until or now < self.next_send_at:
            return
        while self.sent_times and now - self.sent_times[0] > 60:
            self.sent_times.popleft()
        if len(self.sent_times) >= self.cfg.farm.max_messages_per_minute:
            return
        due = [j for j in self.jobs.values() if j.next_due <= now and (j.ready is None or j.ready())]
        if due:
            await self._send(min(due, key=lambda j: (j.priority, j.next_due)), now)

    def _expire_pauses(self, now: float) -> None:
        for reason in [r for r, p in self.pauses.items() if p.until is not None and now >= p.until]:
            self.resume(reason)

    def _check_quiet(self, now: float) -> None:
        windows = self.cfg.safety.quiet_windows
        if not windows:
            return
        local = dt.datetime.fromtimestamp(now)
        quiet = in_quiet(local.hour * 60 + local.minute, windows)
        if quiet and "quiet" not in self.pauses:
            self.pause("quiet", note="quiet hours")
        elif not quiet and "quiet" in self.pauses:
            self.resume("quiet")

    def _check_break(self, now: float) -> None:
        """Work / rest cycle: after `break_every_minutes` of running time, rest for `break_minutes`."""
        saf = self.cfg.safety
        elapsed = min(max(now - self._last_tick, 0.0), 5.0) if self._last_tick else 0.0
        self._last_tick = now
        if not saf.break_every_minutes or self.pauses:      # only time spent running counts towards the next break
            return
        self._work += elapsed
        if self._work >= saf.break_every_minutes * 60:
            self._work = 0.0
            self.stats.breaks += 1
            self.pause("break", saf.break_minutes * 60, f"scheduled break, back in {saf.break_minutes:g} min", "info")

    def next_break_in(self, now: float) -> Optional[float]:
        every = self.cfg.safety.break_every_minutes
        if not every or "break" in self.pauses:
            return None
        return max(0.0, every * 60 - self._work)

    def _check_limits(self, now: float) -> None:
        s = self.cfg.safety
        if s.max_runtime_minutes and now - self.started_at >= s.max_runtime_minutes * 60:
            self.stop("runtime limit reached")
        elif s.max_commands and self.stats.sent >= s.max_commands:
            self.stop("command limit reached")

    def _watchdog(self, now: float) -> None:
        timeout = self.cfg.farm.reply_timeout
        while self.awaiting and now - self.awaiting[0].at > timeout:
            lost = self.awaiting.popleft()
            if self.last_reply_at >= lost.at:
                continue          # OwO answered something after this, so it is alive; this one just had no reply
            self.stats.missed += 1
            self.missed += 1
            self.log("warn", f"OwO did not answer '{lost.job}'")
        if self.missed >= self.cfg.farm.max_missed_replies and not self.pauses:
            self.no_reply_pauses += 1
            self.missed = self.cfg.farm.max_missed_replies - 1
            if self.no_reply_pauses > 3:
                self.stop("OwO keeps ignoring us (wrong channel, bot offline or unseen captcha)", EXIT_SAFETY, True)
                return
            wait = 180.0 * self.no_reply_pauses
            self.pause("no_reply", wait, f"OwO stopped answering; checking again in {fmt_dur(wait)}")
            self.alerts.notify("no_reply", "OwO stopped answering", "Check the channel for a captcha.", urgent=True)

    def _captcha_housekeeping(self, now: float) -> None:
        pause = self.pauses.get("captcha")
        if pause is None or pause.until is not None:   # a timed pause expires by itself
            return
        saf = self.cfg.safety
        if self.captcha_resume_at is not None and now >= self.captcha_resume_at:
            self.captcha_resume_at = None
            self.resume("captcha")
            return
        if self.captcha_resume_at is None and now - self.captcha_alert_at >= saf.captcha_remind_seconds:
            self.captcha_alert_at = now
            self.alerts.notify("captcha", "Captcha waiting", "Solve it in Discord; the farm resumes by itself.", urgent=True)
        if saf.captcha_give_up_minutes and self.captcha_resume_at is None \
                and now - self.captcha_since >= saf.captcha_give_up_minutes * 60:
            self.stop("captcha was not solved in time", EXIT_SAFETY, True)

    async def _send(self, job: Job, now: float) -> None:
        text = job.build()
        if text is None:
            job.next_due = now + 20
            return
        full = text if job.raw else f"{self.prefix} {text}"
        entry = Awaiting(job.id, now)
        if job.expects_reply:
            self.awaiting.append(entry)
        try:
            await self.transport.send(full)
        except SendError as exc:
            with contextlib.suppress(ValueError):
                self.awaiting.remove(entry)
            self._send_failed(job, exc, now)
            return
        self.send_errors = 0
        self.stats.sent += 1
        self.last_job = (job.id, now)
        job.sent += 1
        job.last_sent = now
        self.sent_times.append(now)
        self._recent_own[normalize(full)] = now
        for key in [k for k, t in self._recent_own.items() if now - t > 60]:
            del self._recent_own[key]
        if job.on_sent:
            job.on_sent(now)
        job.next_due = job.after_send(now) if job.after_send else now + self.rng.uniform(*job.cooldown)
        self.next_send_at = now + self.gap
        self.log("info", f"sent: {full}")

    def _send_failed(self, job: Job, exc: SendError, now: float) -> None:
        self.stats.errors += 1
        if exc.kind == "forbidden":
            self.stop("Discord says we cannot send messages in this channel", EXIT_CHANNEL, True)
        elif exc.kind == "captcha_required":
            self.stop("Discord is asking this account for a captcha; log in to Discord normally and solve it", EXIT_SAFETY, True)
        elif exc.kind == "ratelimit":
            self.pause("ratelimit", exc.retry_after + 2, "Discord rate limit")
        else:
            self.send_errors += 1
            job.next_due = now + 15
            self.log("warn", f"send failed ({exc.kind}): {exc}")
            if self.send_errors >= 5:
                self.pause("errors", 300, "repeated send errors")

    # ---- incoming messages ---------------------------------------------------------

    def _name_match(self, text: str) -> bool:
        if self.me_id and (f"<@{self.me_id}>" in text or f"<@!{self.me_id}>" in text):
            return True                  # OwO's newer replies mention you instead of writing your name
        sq = squash(text)
        return any(n in sq for n in self.names)

    async def handle(self, m: Incoming) -> None:
        if self.stopped:
            return
        now = self.clock()
        if m.from_me:
            pinger = getattr(self.alerts, "pinger", None)
            if pinger is not None and pinger.wants_stop(m):
                pinger.stop("you replied stop")
                return
            self._own_message(m, now)
            return
        if m.author_id != OWO_ID or (not m.is_dm and m.channel_id != self.channel_id):
            return
        facts = parse_facts(m.text, verify_button=m.verify_button, warn_attachment=m.warn_attachment)
        mine = m.is_dm or m.mentions_me or m.refs_me or self.trust_channel or self._name_match(m.text)
        if facts.captcha:               # better a needless pause than a missed captcha
            self._on_captcha(m, now, mine)
            return
        if not mine:
            return
        if facts.ban:
            self.stop("OwO says this account has been banned", EXIT_SAFETY, True)
            return
        if facts.verified:
            self._on_verified(now)
            return
        if m.is_dm:
            return

        culprit = None if m.edited else self._on_reply(now)
        if facts.cooldown:
            self._on_cooldown(facts, culprit or self._recent_job(now), now)
        if facts.throttled:
            self._on_throttle(now)
        if facts.no_cowoncy:
            self._on_no_cowoncy(culprit or self._recent_job(now), now)
        if facts.cash is not None:
            self._on_cash(facts.cash)
        if facts.daily_ok is not None:
            self._on_daily_ok(facts.daily_ok, now)
        if facts.wait is not None:
            self._on_wait(facts.wait, culprit, m.text, now)
        if facts.sold:
            self.stats.sold_cowoncy += facts.sold
            if self.stats.cash is not None:
                self.stats.cash += facts.sold
        if not m.edited:
            if facts.hunt:
                self.stats.hunts += 1
                self.stats.xp += facts.xp
                self.stats.animals += facts.animals
                self.gem_levels = dict(facts.gem_levels)
                if self.stats.cash is not None:
                    self.stats.cash = max(0, self.stats.cash - 5)    # every hunt costs 5 cowoncy
                if self.gems and self.gems.on_hunt(facts.gems_active, now):
                    self.jobs["inv"].next_due = now + self.rng.uniform(1.0, 3.0)
            if facts.battle:
                self.stats.battles += 1
            if facts.pray:
                self.stats.prays += 1
            if facts.curse:
                self.stats.curses += 1
        if self.gems:
            self._gem_replies(facts, now)
        if self.gambler:
            self._gamble_replies(m, facts, now)

    def _on_reply(self, now: float) -> Optional[str]:
        """OwO said something to us. Returns the command it most likely answers (None if unclear)."""
        self.stats.replies += 1
        self.missed = 0
        self.no_reply_pauses = 0
        self.last_reply_at = now
        while self.awaiting:
            entry = self.awaiting.popleft()
            if now - entry.at <= 8.0:         # older ones never got an answer; drop them quietly
                return entry.job
        return None

    def _recent_job(self, now: float) -> Optional[str]:
        job, at = self.last_job
        return job if job and now - at <= 8.0 else None

    def _on_captcha(self, m: Incoming, now: float, mine: bool) -> None:
        if not mine:
            self.pause("captcha", 120, "captcha text not clearly addressed to you")
            return
        first = "captcha" not in self.pauses or self.pauses["captcha"].until is not None
        self.captcha_resume_at = None
        self.pauses.pop("captcha", None)
        self.pause("captcha", note="solve the captcha in Discord")
        if first:
            self.stats.captchas += 1
            self.captcha_since = self.captcha_alert_at = now
            where = f" {m.url}" if m.url else ""
            self.log("error", f"CAPTCHA from OwO{where}")
            self.alerts.notify("captcha", "OwO captcha!", f"Solve it in Discord.{where}", urgent=True)

    def _on_verified(self, now: float) -> None:
        self.alerts.notify("verified", "Captcha solved", "")
        if "captcha" not in self.pauses:
            return
        delay = self.rng.uniform(*self.cfg.safety.captcha_resume_delay)
        self.captcha_resume_at = now + delay
        self.log("info", f"OwO says you are verified; resuming in {delay:.0f}s")

    def _note_slowdown(self, now: float) -> None:
        s = self.cfg.safety
        self.slow_events.append(now)
        while self.slow_events and now - self.slow_events[0] > s.slowdown_window_minutes * 60:
            self.slow_events.popleft()
        if len(self.slow_events) >= s.slowdown_limit:
            self.slow_events.clear()
            wait = self.rng.uniform(*s.slowdown_pause)
            self.pause("slowdown", wait, "OwO keeps saying slow down")
            self.alerts.notify("slowdown", "OwO says slow down", f"Pausing for {fmt_dur(wait)}.")

    def _on_cooldown(self, f: Facts, culprit: Optional[str], now: float) -> None:
        wait = max(1.0, f.cooldown_until - now) if f.cooldown_until else 6.0
        job = self.jobs.get(culprit or "")
        if job:
            job.next_due = max(job.next_due, now + min(wait, 3600) + self.rng.uniform(1.0, 3.0))
        self.stats.slowdowns += 1
        self.hold_until = max(self.hold_until, now + self.rng.uniform(2.0, 4.0))
        self.boost, self.boost_until = 1.5, now + 90
        self.log("warn", f"cooldown notice for {culprit or 'a command'} ({wait:.0f}s)")
        self._note_slowdown(now)

    def _on_throttle(self, now: float) -> None:
        self.stats.slowdowns += 1
        self.hold_until = max(self.hold_until, now + self.rng.uniform(8.0, 15.0))
        self.boost, self.boost_until = 2.0, now + 180
        self.log("warn", "OwO says we are too fast; backing off")
        self._note_slowdown(now)

    def _on_no_cowoncy(self, culprit: Optional[str], now: float) -> None:
        bet = self.gambler.pending if self.gambler else None
        if self.gambler and (culprit == "gamble" or (bet is not None and now - bet.at < 10)):
            self.gambler.pending = None
            self.gambler.halt("out of cowoncy", now, self.cfg.gamble.broke_pause_minutes)
            if "cash" in self.jobs:
                self.jobs["cash"].next_due = now + 5
        elif culprit == "hunt" and "hunt" in self.jobs:
            self.jobs["hunt"].next_due = now + 600
            self.log("warn", "not enough cowoncy to hunt; trying again in 10 minutes")
        else:
            self.log("warn", "OwO says you do not have enough cowoncy")

    def _on_cash(self, value: int) -> None:
        self.stats.cash = value
        if self.stats.cash_start is None:
            self.stats.cash_start = value
        self.state.set(cash=value)

    def _on_daily_ok(self, amount: int, now: float) -> None:
        self.stats.dailies += 1
        self.stats.daily_cowoncy += amount
        if self.stats.cash is not None:
            self.stats.cash += amount
        nxt = now + seconds_until_reset(now) + self.rng.uniform(30, 300)
        if "daily" in self.jobs:
            self.jobs["daily"].next_due = nxt
        self.state.set(daily_next=nxt)
        self.log("info", f"daily claimed (+{amount:,})")

    def _on_wait(self, seconds: Optional[float], culprit: Optional[str], text: str, now: float) -> None:
        if seconds is None:
            return
        target = culprit if culprit in ("daily", "cookie") else ("cookie" if "\U0001F36A" in text else "daily")
        job = self.jobs.get(target)
        if not job:
            return
        job.next_due = now + seconds + self.rng.uniform(20, 120)
        if target == "daily":
            self.state.set(daily_next=job.next_due)
        self.log("info", f"{target} not ready, retry in {fmt_dur(seconds)}")

    def _gem_replies(self, f: Facts, now: float) -> None:
        gems = self.gems
        if gems is None:
            return
        if f.inventory and gems.on_inventory(f.inventory, now):
            self.jobs["use"].next_due = now + self.rng.uniform(1.0, 3.0)
        elif f.inventory and gems.note:
            self.log("info", gems.note)
            gems.note = ""
        if f.gem_used or f.gem_refused:
            self.log("info", "gems activated" if f.gem_used else "OwO refused the gem (already active or none owned)")
            gems.done()

    def _gamble_replies(self, m: Incoming, f: Facts, now: float) -> None:
        g = self.gambler
        game = self.cfg.gamble.game
        result = f.cf if game == "coinflip" else f.slots if game == "slots" else None
        if g is None or result is None or m.id in self._resolved:
            return
        delta = g.on_result(result[0], result[1], now)
        if delta is None:
            return
        self._resolved.append(m.id)
        if self.stats.cash is not None:
            self.stats.cash += delta
        word = "won" if delta > 0 else "lost" if delta < 0 else "pushed"
        self.log("info", f"{game}: {word} {abs(delta):,} (net {g.net:+,})")

    def _own_message(self, m: Incoming, now: float) -> None:
        if m.channel_id != self.channel_id or not self.cfg.farm.sync_manual_commands:
            return
        n = normalize(m.text)
        when = self._recent_own.pop(n, None)
        if when is not None and now - when < 30:
            return
        word = n
        if n.startswith(self.prefix.casefold()):
            word = n[len(self.prefix):].strip()
        cmd = word.split(" ", 1)[0] if word else ""
        job = self.jobs.get(ALIASES.get(cmd, ""))
        if not job:
            return
        job.next_due = max(job.next_due, job.after_manual(now) if job.after_manual
                           else now + self.rng.uniform(*job.cooldown))
        self.stats.manual += 1
        self.log("info", f"you ran '{cmd}' yourself; timer reset")


# ======================================================================================
# 6. Offline simulator (used by --simulate and by the tests)
# ======================================================================================

class SimClock:
    def __init__(self, start: float = 1_780_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class SimOwO:
    """A small stand-in for the OwO bot that answers in OwO's real message formats."""

    COOLDOWN = {"hunt": 15, "battle": 15, "pray": 300, "cf": 15, "slots": 15, "cash": 5, "lootbox": 5,
                "crate": 30, "cookie": 5, "sell": 1, "sac": 1, "inv": 10, "use": 1}
    CANON = {"h": "hunt", "b": "battle", "curse": "pray", "s": "slots", "lb": "lootbox", "wc": "crate"}
    COW = "<:cowoncy:416043450337853441>"

    def __init__(self, farmer: Farmer, clock: SimClock, rng: random.Random, name: str = "Tester",
                 cash: int = 5000, channel_id: int = 1):
        self.farmer, self.clock, self.rng, self.name = farmer, clock, rng, name
        self.cash = cash
        self.channel_id = channel_id
        self.last: Dict[str, float] = {}
        self.queue: List[Tuple[float, int, Incoming]] = []
        self.ids = itertools.count(1000)
        self.counter = itertools.count()
        self.captcha_active = False
        self.last_daily_day = ""
        self.hunts = 0
        self.daily_total = 0
        self.sent_log: List[Tuple[float, str]] = []
        self.inventory: Dict[str, int] = {"051": 3, "052": 2, "065": 2, "067": 1, "072": 2, "074": 1}
        self.active_gems: Dict[str, int] = {}          # gem type -> hunts left
        self.hunts_without: Dict[str, int] = {k: 0 for k in GEM_BASE}   # hunts that ran without that gem type

    GEM_EMOJI = {"hunting": "gem1", "empowering": "gem3", "lucky": "gem4", "special": "star"}

    @staticmethod
    def _small(n: int) -> str:
        return "".join("⁰¹²³⁴⁵⁶⁷⁸⁹"[int(c)] for c in f"{n:02d}")

    def _gem_kind(self, gid: str) -> Tuple[str, str]:
        number = int(gid)
        kind = max((k for k in GEM_BASE if GEM_BASE[k] <= number), key=lambda k: GEM_BASE[k])
        return kind, TIERS[number - GEM_BASE[kind]]

    def _gem_emoji(self, kind: str, tier: str = "common") -> str:
        return f"<:{tier[0]}{self.GEM_EMOJI[kind]}:{GEM_BASE[kind]}>"

    def _push(self, delay: float, text: str, *, edited: bool = False, dm: bool = False, mid: Optional[int] = None) -> int:
        mid = mid or next(self.ids)
        msg = Incoming(mid, 0 if dm else self.channel_id, OWO_ID, text, is_dm=dm, edited=edited)
        heapq.heappush(self.queue, (self.clock() + delay, next(self.counter), msg))
        return mid

    async def deliver(self) -> None:
        while self.queue and self.queue[0][0] <= self.clock():
            _, _, msg = heapq.heappop(self.queue)
            await self.farmer.handle(msg)

    def trigger_captcha(self) -> None:
        self.captcha_active = True
        self._push(0.5, f"**⚠️ | {self.name}**, are you a real human? Please use the link below so I can check! (1/5)")

    def solve_captcha(self) -> None:
        self.captcha_active = False
        self._push(0.5, "**✅ |** I have verified that you are human! Thank you! :3", dm=True)

    def on_send(self, text: str) -> None:
        now = self.clock()
        self.sent_log.append((now, text))
        n = normalize(text)
        if n in ("owo", "uwu"):
            return
        toks = n.split()
        if toks and toks[0] == self.farmer.prefix.casefold():
            toks = toks[1:]
        if not toks:
            return
        cmd = self.CANON.get(toks[0], toks[0])
        args = toks[1:]
        if self.captcha_active:
            self._push(0.8, f"**⚠️ | {self.name}**, please complete your captcha to verify that you are human! (2/5)")
            return
        cd = self.COOLDOWN.get(cmd)
        if cd and now - self.last.get(cmd, -1e9) < cd:
            ts = int(self.last[cmd] + cd) + 1
            self._push(0.8, f"**⏱ | {self.name}**! Slow down and try the command again **<t:{ts}:R>**")
            return
        self.last[cmd] = now
        cow, name = self.COW, self.name
        if cmd == "hunt":
            if self.cash < 5:
                self._push(0.8, f"**\U0001F6AB | {name}**, You don't have enough cowoncy!")
                return
            self.cash -= 5
            self.hunts += 1
            for kind in GEM_BASE:
                if kind not in self.active_gems:
                    self.hunts_without[kind] += 1
            if self.active_gems:
                for kind in self.active_gems:
                    self.active_gems[kind] -= 1                    # spend one use first, then show what is left
                shown = " ".join(f"{self._gem_emoji(k)}`[{left}/25]`" for k, left in self.active_gems.items())
                for kind in [k for k, left in self.active_gems.items() if left <= 0]:
                    del self.active_gems[kind]                     # shown as [0/25] this once, then gone
                self._push(0.8, f"**\U0001F331 | {name}**, hunt is empowered by {shown}  !\n"
                                "<:blank:427371936482328596> **|** You found:  :bee: :beetle: :rabbit2:\n"
                                "<:blank:427371936482328596> **|** <a:2024may_esd:1242757627125956662> gained **13xp**!")
            else:
                self._push(0.8, f"**\U0001F331 | {name}** spent 5 {cow} and caught a **bunny** \U0001F430!")
        elif cmd == "battle":
            self._push(0.8, f"**⚔ | {name}** goes into battle!")
        elif cmd == "pray":
            self._push(0.8, f"**\U0001F64F | {name}** prays... You feel lucky!\n**<:blank:427371936482328596> |** You have **3** luck point(s)!")
        elif cmd == "daily":
            day = pacific_now(now).date().isoformat()
            if day != self.last_daily_day:
                self.last_daily_day = day
                self.cash += 525
                self.daily_total += 525
                self._push(0.8, f"**\U0001F4B0 | {name}**, Here is your daily **{cow} 525 Cowoncy**!")
            else:
                left = int(seconds_until_reset(now))
                self._push(0.8, f"**⏱ |** Nu! **{name}**! You need to wait **{left // 3600}H {left % 3600 // 60}M {left % 60}S**")
        elif cmd == "cash":
            self._push(0.8, f"**{cow} | {name}**, you currently have **__{self.cash:,}__ cowoncy!**")
        elif cmd == "cf":
            bet = int(args[0]) if args and args[0].isdigit() else 1
            if bet > self.cash:
                self._push(0.8, f"**\U0001F6AB | {name}**, You don't have enough cowoncy!")
                return
            win = self.rng.random() < 0.5
            self.cash += bet if win else -bet
            head = f"**{name}** spent **{cow} {bet:,}** and chose **heads**\nThe coin spins... "
            mid = self._push(0.8, head + "<a:coinflip:436677458339823636>")
            tail = f"and you won **{cow} {bet * 2:,}**!!" if win else "and you lost it all... :c"
            self._push(2.8, head + "<:head:436677933977960478> " + tail, edited=True, mid=mid)
        elif cmd == "slots":
            bet = int(args[0]) if args and args[0].isdigit() else 1
            if bet > self.cash:
                self._push(0.8, f"**\U0001F6AB | {name}**, You don't have enough cowoncy!")
                return
            payout = self.rng.choice([0, 0, 0, 0, bet, bet * 2, bet * 3])
            self.cash += payout - bet
            head = f"**\U0001F3B0 | {name}** bet {cow} {bet:,}\n`___SLOTS___`\n"
            mid = self._push(0.8, head + "  `| <a:slot_gif> |`")
            tail = "and won nothing... :c" if payout == 0 else f"and won {cow} {payout:,}"
            self._push(2.8, head + f"  `|  \U0001F352 \U0001F352 \U0001F352 |`   {tail}", edited=True, mid=mid)
        elif cmd == "inv":
            rows = [f"`{gid}`{self._gem_emoji(*self._gem_kind(gid))}{self._small(n)}"
                    for gid, n in sorted(self.inventory.items()) if n > 0]
            self._push(0.8, f"**\U0001F4E6 | {name}'s Inventory**\n" + "  ".join(rows))
        elif cmd == "use":
            lines = []
            for token in args:
                gid = f"{int(token):03d}" if token.isdigit() else ""
                kind, tier = self._gem_kind(gid) if gid else ("", "")
                if gid and self.inventory.get(gid, 0) > 0 and kind not in self.active_gems:
                    self.inventory[gid] -= 1
                    self.active_gems[kind] = 25
                    lines.append(f"**{self._gem_emoji(kind, tier)} | {name}**, you activated a(n) **{tier.title()} {kind.title()} Gem**!")
                else:
                    lines.append("**\U0001F6AB |** you already have an active gem of that type or you do not own this gem!")
            self._push(0.8, "\n".join(lines))
        elif cmd in ("lootbox", "crate", "sell", "sac", "cookie"):
            self._push(0.8, f"**\U0001F4E6 | {name}** done.")


class SimTransport:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.sim: Optional[SimOwO] = None
        self.sent: List[Tuple[float, str]] = []
        self.fail_next: Optional[SendError] = None

    async def send(self, text: str) -> None:
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            raise err
        self.sent.append((self.clock(), text))
        if self.sim:
            self.sim.on_send(text)


async def run_simulation(cfg: SimpleNamespace, minutes: float, seed: int = 1,
                         captcha_at: Optional[float] = None, solve_after: float = 120.0) -> Tuple[Farmer, SimOwO]:
    clock = SimClock()
    rng = random.Random(seed)
    transport = SimTransport(clock)
    farmer = Farmer(cfg, transport, clock=clock, rng=rng)
    farmer.channel_id = 1
    sim = SimOwO(farmer, clock, rng, "Tester", channel_id=1)
    transport.sim = sim
    farmer.set_identity(1, ["Tester"])
    farmer.start()
    end = clock() + minutes * 60
    captcha_t = clock() + captcha_at * 60 if captcha_at is not None else None
    solve_t = None
    while clock() < end and not farmer.stopped:
        clock.advance(0.25)
        if captcha_t is not None and clock() >= captcha_t:
            captcha_t, solve_t = None, clock() + solve_after
            sim.trigger_captcha()
        if solve_t is not None and clock() >= solve_t:
            solve_t = None
            sim.solve_captcha()
        await sim.deliver()
        await farmer.tick()
    return farmer, sim


# ======================================================================================
# 7. Discord glue (imports discord lazily so everything above works without it)
# ======================================================================================

class LateTransport:
    def __init__(self) -> None:
        self.inner: Any = None

    async def send(self, text: str) -> None:
        if self.inner is None:
            raise SendError("network", "not connected yet")
        await self.inner.send(text)


def discord_library_problem() -> Optional[str]:
    """None when discord.py-self is usable, otherwise a message that says how to fix it."""
    try:
        import discord
    except ModuleNotFoundError:
        return "discord.py-self is not installed. Run:  pip install -r requirements.txt"
    if not hasattr(discord, "CaptchaRequired"):
        return ("The installed 'discord' package is the official discord.py (for bots), not discord.py-self.\n"
                "Fix it with:  pip uninstall -y discord.py   then   pip install -r requirements.txt\n"
                "(start.bat / start.sh do this for you, in a private environment of their own)")
    return None


class DiscordTransport:
    def __init__(self, channel: Any, silent: bool):
        self.channel, self.silent = channel, silent

    async def send(self, text: str) -> None:
        import aiohttp
        import discord
        captcha_required = getattr(discord, "CaptchaRequired", ())     # () matches nothing in an except clause
        rate_limited = getattr(discord, "RateLimited", ())
        try:
            await self.channel.send(text, silent=self.silent)
        except captcha_required as exc:
            raise SendError("captcha_required", str(exc)) from exc
        except discord.Forbidden as exc:
            raise SendError("forbidden", str(exc)) from exc
        except rate_limited as exc:
            raise SendError("ratelimit", str(exc), float(getattr(exc, "retry_after", 5) or 5)) from exc
        except discord.HTTPException as exc:
            if exc.status == 429:
                raise SendError("ratelimit", str(exc), float(getattr(exc, "retry_after", 5) or 5)) from exc
            raise SendError("http", f"{exc.status} {exc.text}") from exc
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise SendError("network", str(exc)) from exc


def _walk_components(comps: Any, texts: List[str], labels: List[str]) -> None:
    """Collect text from message components, including the newer 'components v2' layout."""
    for comp in comps or []:
        if not isinstance(comp, dict):
            continue
        kind = comp.get("type")
        if kind == 10 and comp.get("content"):            # text display
            texts.append(str(comp["content"]))
        elif kind == 2 and comp.get("label"):             # button
            labels.append(str(comp["label"]))
        elif kind == 18 and isinstance(comp.get("label"), str):
            texts.append(comp["label"])
        _walk_components(comp.get("components"), texts, labels)
        for key in ("accessory", "component"):
            if isinstance(comp.get(key), dict):
                _walk_components([comp[key]], texts, labels)


def payload_text(d: Dict[str, Any]) -> Tuple[str, bool]:
    """All readable text of a raw gateway message, and whether it has a 'Verify' button."""
    parts: List[str] = [str(d.get("content") or "")]
    for emb in d.get("embeds") or []:
        if not isinstance(emb, dict):
            continue
        parts += [str(emb.get("title") or ""), str(emb.get("description") or "")]
        parts += [f"{f.get('name', '')} {f.get('value', '')}" for f in emb.get("fields") or [] if isinstance(f, dict)]
        parts.append(str((emb.get("footer") or {}).get("text") or ""))
        parts.append(str((emb.get("author") or {}).get("name") or ""))
    texts: List[str] = []
    labels: List[str] = []
    _walk_components(d.get("components"), texts, labels)
    parts += texts
    verify = any(label.strip().casefold() == "verify" for label in labels)
    return "\n".join(p for p in parts if p), verify


def incoming_from_payload(d: Dict[str, Any], me_id: int, edited: bool = False) -> Optional[Incoming]:
    """Build an Incoming from the 'd' of a MESSAGE_CREATE / MESSAGE_UPDATE gateway event."""
    author = d.get("author") or {}
    try:
        aid, cid, mid = int(author.get("id") or 0), int(d.get("channel_id") or 0), int(d.get("id") or 0)
    except (TypeError, ValueError):
        return None
    if not (aid and cid and mid):          # partial updates (embed unfurls etc.) carry no author
        return None
    text, verify = payload_text(d)
    ref = d.get("referenced_message")
    ref_author = int(((ref or {}).get("author") or {}).get("id") or 0) if isinstance(ref, dict) else 0
    guild = d.get("guild_id")
    return Incoming(
        id=mid, channel_id=cid, author_id=aid, text=text, is_dm=not guild, from_me=aid == me_id,
        mentions_me=(any(str(u.get("id")) == str(me_id) for u in d.get("mentions") or [] if isinstance(u, dict))
                     or f"<@{me_id}>" in text or f"<@!{me_id}>" in text),
        refs_me=bool(ref_author) and ref_author == me_id, edited=edited, verify_button=verify,
        warn_attachment=("⚠" in text and bool(d.get("attachments"))),
        url=f"https://discord.com/channels/{guild or '@me'}/{cid}/{mid}",
    )


def to_incoming(message: Any, me_id: int, edited: bool = False) -> Incoming:
    parts = [message.content or ""]
    for emb in getattr(message, "embeds", []) or []:
        parts += [emb.title or "", emb.description or ""]
        parts += [f"{f.name} {f.value}" for f in getattr(emb, "fields", [])]
        if getattr(emb, "footer", None) and emb.footer.text:
            parts.append(emb.footer.text)
        if getattr(emb, "author", None) and emb.author.name:
            parts.append(emb.author.name)
    text = "\n".join(p for p in parts if p)
    verify = False
    for row in getattr(message, "components", []) or []:
        for child in getattr(row, "children", []) or []:
            if str(getattr(child, "label", "") or "").casefold() == "verify":
                verify = True
    ref = getattr(message, "reference", None)
    resolved = getattr(ref, "resolved", None)
    refs_me = getattr(getattr(resolved, "author", None), "id", None) == me_id
    return Incoming(
        id=message.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        text=text,
        is_dm=message.guild is None,
        from_me=message.author.id == me_id,
        mentions_me=any(u.id == me_id for u in getattr(message, "mentions", [])) or f"<@{me_id}>" in text,
        refs_me=bool(refs_me),
        edited=edited,
        verify_button=verify,
        warn_attachment=("⚠" in text and bool(getattr(message, "attachments", None))),
        url=getattr(message, "jump_url", "") or "",
    )


# ======================================================================================
# 8. Terminal dashboard and hotkeys
# ======================================================================================

def render_dashboard(f: Farmer) -> Any:
    from rich import box
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    now = f.clock()
    colour = {"running": "bold green", "paused": "bold yellow", "stopped": "bold red"}[f.status]
    head = Text.assemble(("OwO Farmer ", "bold"), (f"v{__version__}   ", "dim"), (f.status.upper(), colour),
                         (f"   up {fmt_dur(now - f.started_at)}   sent {f.stats.sent}", "dim"))
    next_break = f.next_break_in(now)
    if next_break is not None:
        head.append(f"   next break in {fmt_dur(next_break)}", style="dim")
    if f.pauses:
        head.append("\n" + "; ".join(f"{p.reason}" + (f" ({fmt_dur(p.until - now)})" if p.until else "") +
                                      (f": {p.note}" if p.note else "") for p in f.pauses.values()), style="yellow")

    jobs = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False)
    for col, just in (("Command", "left"), ("Next", "right"), ("Sent", "right")):
        jobs.add_column(col, justify=just)
    for job in sorted(f.jobs.values(), key=lambda j: j.priority):
        if job.id in ("inv", "use") and job.next_due > now + 1800:
            continue
        due = job.next_due - now
        jobs.add_row(job.label, ("paused" if f.pauses else "now") if due <= 0 else fmt_dur(due), str(job.sent))

    s = f.stats
    delta = f"  ({s.cash - s.cash_start:+,})" if s.cash is not None and s.cash_start is not None else ""
    facts = Table.grid(padding=(0, 2))
    facts.add_row("hunts", str(s.hunts), "battles", str(s.battles), "prays", str(s.prays + s.curses))
    facts.add_row("daily", f"{s.dailies} (+{s.daily_cowoncy:,})", "captchas", str(s.captchas), "slow-downs", str(s.slowdowns))
    facts.add_row("cowoncy", (f"{s.cash:,}" if s.cash is not None else "?") + delta, "replies", f"{s.replies}/{s.missed} missed",
                  "manual", str(s.manual))
    if f.gems:
        facts.add_row("gems", {"idle": "keeping up", "inventory": "checking bag", "use": "using"}[f.gems.state],
                      "waiting" if now < f.gems.empty_until else "", f"until {fmt_dur(f.gems.empty_until - now)}"
                      if now < f.gems.empty_until else "")
    body: List[Any] = [Panel(head, border_style="cyan"), Panel(jobs, title="Schedule"), Panel(facts, title="Session")]
    if f.gambler:
        g = f.gambler
        body.append(Panel(Text(f"{f.cfg.gamble.game} / {f.cfg.gamble.strategy}   bet {g.current_bet:,}   streak {g.streak}   "
                               f"W/L {g.wins}/{g.losses}   net {g.net:+,}   today {g.net_today(now):+,}\n{g.status(now)}"),
                         title="Gambling"))
    log = Text()
    for ev in list(f.events)[-10:]:
        style = {"error": "red", "warn": "yellow"}.get(ev.level, "")
        log.append(time.strftime("%H:%M:%S ", time.localtime(ev.t)), style="dim")
        log.append(ev.text + "\n", style=style)
    body.append(Panel(log, title="Log"))
    keys = "[p] pause/resume   [c] captcha solved   [g] gambling on/off   [q] quit"
    body.append(Text(keys + (f"\nweb dashboard: {f.web_url}" if f.web_url else ""), style="dim"))
    return Group(*body)


# ======================================================================================
# 8b. Web dashboard (read-only, served by this process)
# ======================================================================================

def snapshot(f: Farmer) -> Dict[str, Any]:
    """Everything the web dashboard shows, as plain data. Never contains tokens or passwords."""
    now = f.clock()
    s = f.stats
    jobs = []
    for job in sorted(f.jobs.values(), key=lambda j: j.priority):
        if job.id in ("inv", "use") and job.next_due > now + 1800:
            continue
        jobs.append({"id": job.id, "label": job.label, "sent": job.sent,
                     "next_in": max(0.0, job.next_due - now) if f.started_at else None})
    gems = None
    if f.gems:
        gems = {"state": f.gems.state, "wanted": list(f.cfg.gems.types),
                "levels": {k: {"left": left, "total": total} for k, (left, total) in f.gem_levels.items()},
                "retry_in": max(0.0, f.gems.empty_until - now)}
    gamble = None
    if f.gambler:
        g = f.gambler
        gamble = {"game": f.cfg.gamble.game, "strategy": f.cfg.gamble.strategy, "bet": g.current_bet, "streak": g.streak,
                  "bets": g.bets, "wins": g.wins, "losses": g.losses, "pushes": g.pushes, "net": g.net,
                  "today": g.net_today(now), "status": g.status(now)}
    saf = f.cfg.safety
    pinger = getattr(f.alerts, "pinger", None)
    return {
        "version": __version__,
        "status": f.status,
        "uptime": max(0.0, now - f.started_at) if f.started_at else 0.0,
        "channel_id": str(f.channel_id),
        "pauses": [{"reason": p.reason, "note": p.note, "ends_in": max(0.0, p.until - now) if p.until else None}
                   for p in f.pauses.values()],
        "stats": {k: getattr(s, k) for k in ("sent", "replies", "missed", "hunts", "battles", "prays", "curses", "dailies",
                                             "daily_cowoncy", "captchas", "slowdowns", "manual", "errors", "xp", "animals",
                                             "breaks", "cash", "cash_start")},
        "jobs": jobs,
        "gems": gems,
        "gamble": gamble,
        "break": {"enabled": bool(saf.break_every_minutes), "every_minutes": saf.break_every_minutes,
                  "minutes": saf.break_minutes, "next_in": f.next_break_in(now), "on_break": "break" in f.pauses},
        "dm_alerts": {"enabled": pinger is not None, "pinging": bool(pinger and pinger.active),
                      "pings": pinger.pings if pinger else 0, "silenced": bool(pinger and pinger.silenced),
                      "dead": bool(pinger and pinger.dead)},
        "events": [{"t": e.t, "level": e.level, "text": e.text} for e in list(f.notable)[-60:]],
        "commands": [{"t": e.t, "text": e.text[6:]} for e in f.events if e.text.startswith("sent: ")][-12:],
    }


INDEX_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OwO Farmer</title>
<style>
:root{color-scheme:dark;--bg:#0e1015;--card:#171a22;--line:#262b38;--text:#e8eaf0;--mute:#8d96ab;--ok:#3ddc84;--warn:#ffc24b;--bad:#ff5d5d;--acc:#7aa7ff}
@media (prefers-color-scheme:light){:root{color-scheme:light;--bg:#f3f5f9;--card:#fff;--line:#e1e5ee;--text:#1a1f2e;--mute:#667088;--acc:#2f63d9}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line)}
h1{font-size:17px;margin:0}
.pill{padding:3px 11px;border-radius:99px;font-weight:600;font-size:12px;letter-spacing:.04em;background:var(--line)}
.running{background:rgba(61,220,132,.16)!important;color:var(--ok)}
.paused{background:rgba(255,194,75,.16)!important;color:var(--warn)}
.stopped,.offline{background:rgba(255,93,93,.16)!important;color:var(--bad)}
.mute{color:var(--mute)}
main{display:grid;gap:14px;padding:16px 18px;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));max-width:1180px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);margin:0 0 10px;font-weight:600}
.wide{grid-column:1/-1}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px}
.tile b{display:block;font-size:20px;font-variant-numeric:tabular-nums}
.tile span{color:var(--mute);font-size:12px}
table{width:100%;border-collapse:collapse}
td,th{padding:5px 4px;text-align:left;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{color:var(--mute);font-weight:500;font-size:12px}
.r{text-align:right}
.gem{margin:10px 0}
.gem .row{display:flex;justify-content:space-between;margin-bottom:4px}
.bar{height:10px;border-radius:6px;background:var(--line);overflow:hidden}
.bar i{display:block;height:100%;background:var(--acc)}
.bar.low i{background:var(--warn)}
.bar.out i{background:var(--bad)}
.mono{font:12px/1.55 ui-monospace,Menlo,Consolas,monospace;max-height:300px;overflow:auto}
.mono div{white-space:pre-wrap;word-break:break-word}
.mono:empty::before{content:"nothing yet";color:var(--mute)}
.warn{color:var(--warn)}.error{color:var(--bad)}
[hidden]{display:none!important}
</style>
</head>
<body>
<header>
  <h1>OwO Farmer</h1><span id="status" class="pill offline">connecting</span>
  <span id="why" class="mute"></span><span id="up" class="mute" style="margin-left:auto"></span>
</header>
<main>
  <section class="card wide"><h2>Session</h2><div class="tiles" id="tiles"></div></section>
  <section class="card"><h2>Schedule</h2><table id="jobs"></table></section>
  <section class="card" id="gemcard" hidden><h2>Gems</h2><div id="gems"></div></section>
  <section class="card"><h2>Breaks</h2><div id="brk"></div></section>
  <section class="card" id="gamblecard" hidden><h2>Gambling</h2><div id="gamble"></div></section>
  <section class="card"><h2>Alerts</h2><div id="alerts"></div></section>
  <section class="card wide"><h2>Events</h2><div id="log" class="mono"></div></section>
  <section class="card wide"><h2>Last commands</h2><div id="cmds" class="mono"></div></section>
</main>
<script>
const $ = id => document.getElementById(id);
const num = n => (n ?? 0).toLocaleString();
const pad = n => String(n).padStart(2, "0");
const fmt = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return (h ? h + ":" + pad(m) : pad(m)) + ":" + pad(s % 60); };
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function line(label, value) { const d = el("div"); d.append(el("span", "mute", label + "  "), el("span", "", value)); return d; }
function tile(label, value) { const t = el("div", "tile"); t.append(el("b", "", value), el("span", "", label)); return t; }
const signed = n => (n >= 0 ? "+" : "") + num(n);

function render(d) {
  const st = $("status"); st.textContent = d.status.toUpperCase(); st.className = "pill " + d.status;
  $("why").textContent = d.pauses.map(p => p.reason + (p.ends_in != null ? " (" + fmt(p.ends_in) + ")" : "") + (p.note ? ": " + p.note : "")).join("  |  ");
  $("up").textContent = "up " + fmt(d.uptime) + "  |  channel " + d.channel_id + "  |  v" + d.version;

  const s = d.stats;
  const cash = s.cash == null ? "?" : num(s.cash) + (s.cash_start == null ? "" : "  (" + signed(s.cash - s.cash_start) + ")");
  $("tiles").replaceChildren(tile("messages sent", num(s.sent)), tile("hunts", num(s.hunts)), tile("battles", num(s.battles)),
    tile("prays / curses", num(s.prays) + " / " + num(s.curses)), tile("animals caught", num(s.animals)), tile("xp gained", num(s.xp)),
    tile("daily claims", num(s.dailies)), tile("cowoncy", cash), tile("captchas", num(s.captchas)), tile("slow-downs", num(s.slowdowns)),
    tile("replies / missed", num(s.replies) + " / " + num(s.missed)), tile("breaks taken", num(s.breaks)));

  const head = el("tr"); ["Command", "Next", "Sent"].forEach((h, i) => head.append(el("th", i ? "r" : "", h)));
  const rows = [head];
  d.jobs.forEach(j => { const tr = el("tr");
    const next = j.next_in == null ? "-" : j.next_in <= 0 ? (d.status === "running" ? "now" : "waiting") : fmt(j.next_in);
    tr.append(el("td", "", j.label), el("td", "r", next), el("td", "r", num(j.sent))); rows.push(tr); });
  $("jobs").replaceChildren(...rows);

  const g = d.gems; $("gemcard").hidden = !g;
  if (g) {
    const names = {hunting: "Hunting gem", empowering: "Empowering gem", lucky: "Lucky gem", special: "Special gem"};
    const out = g.wanted.map(k => { const lv = g.levels[k]; const box = el("div", "gem");
      const row = el("div", "row"); row.append(el("span", "", names[k] || k),
        el("span", "mute", lv ? num(lv.left) + " / " + num(lv.total) + " uses" : "none active"));
      const pct = lv && lv.total ? Math.max(0, Math.min(100, lv.left / lv.total * 100)) : 0;
      const bar = el("div", "bar" + (!lv || lv.left <= 0 ? " out" : pct <= 15 ? " low" : "")); const fill = el("i"); fill.style.width = pct + "%";
      bar.append(fill); box.append(row, bar); return box; });
    const state = {idle: "keeping up", inventory: "checking your inventory", use: "using a gem"}[g.state] || g.state;
    out.push(line("status", g.retry_in > 0 ? "no usable gems, checking again in " + fmt(g.retry_in) : state));
    $("gems").replaceChildren(...out);
  }

  const b = d.break, bt = [];
  if (!b.enabled) bt.push(line("scheduled breaks", "off"));
  else {
    bt.push(line(b.on_break ? "on break, back in" : "next break in", b.on_break ? fmt((d.pauses.find(p => p.reason === "break") || {}).ends_in || 0) : b.next_in == null ? "-" : fmt(b.next_in)));
    bt.push(line("pattern", "rest " + b.minutes + " min after every " + b.every_minutes + " min of running"));
    bt.push(line("breaks taken", num(s.breaks)));
  }
  $("brk").replaceChildren(...bt);

  const gm = d.gamble; $("gamblecard").hidden = !gm;
  if (gm) $("gamble").replaceChildren(line(gm.game + " / " + gm.strategy, gm.status), line("current bet", num(gm.bet) + "  (loss streak " + gm.streak + ")"),
    line("record", gm.wins + " won, " + gm.losses + " lost, " + gm.pushes + " even"), line("net this session", signed(gm.net)), line("net today", signed(gm.today)));

  const a = d.dm_alerts;
  $("alerts").replaceChildren(
    line("DM alerts", !a.enabled ? "off" : a.dead ? "switched off after errors" : a.pinging ? "pinging you now (" + a.pings + " sent)" : a.silenced ? "you said stop" : "ready"),
    line("captchas so far", num(s.captchas)));

  const when = t => new Date(t * 1000).toLocaleTimeString();
  $("log").replaceChildren(...d.events.slice().reverse().map(e =>
    el("div", e.level === "error" ? "error" : e.level === "warn" ? "warn" : "", when(e.t) + "  " + e.text)));
  $("cmds").replaceChildren(...d.commands.slice().reverse().map(c => el("div", "mute", when(c.t) + "  " + c.text)));
}

let misses = 0;
async function poll() {
  try {
    const r = await fetch("api/stats", {cache: "no-store"});
    if (!r.ok) throw new Error(r.status);
    render(await r.json()); misses = 0;
  } catch (e) {
    if (++misses > 1) { const st = $("status"); st.textContent = "OFFLINE"; st.className = "pill offline"; }
  }
}
poll(); setInterval(poll, 2000);
</script>
</body>
</html>
'''

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class WebDashboard:
    """A small read-only stats page. It is served from the farmer's own event loop and cannot change anything."""

    HEADERS = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
    }

    def __init__(self, farmer: Farmer, cfg: SimpleNamespace):
        self.farmer, self.cfg = farmer, cfg
        self.url = ""
        self.port = 0
        self._runner: Any = None

    def _authorised(self, request: Any) -> bool:
        password = self.cfg.password
        if not password:
            return True
        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            supplied = base64.b64decode(header[6:].strip()).decode("utf-8", "replace").partition(":")[2]
        except ValueError:
            return False
        return hmac.compare_digest(supplied.encode(), password.encode())

    def _host_allowed(self, request: Any) -> bool:
        """When only this computer is meant to see the page, refuse requests that arrive under another name
        (a website can point its own domain at 127.0.0.1 to reach a local server)."""
        if self.cfg.host not in LOOPBACK_HOSTS:
            return True
        host = request.host or ""
        if host.startswith("["):                      # [::1]:8787
            name = host[1:].split("]", 1)[0]
        else:
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in LOOPBACK_HOSTS

    async def start(self) -> str:
        from aiohttp import web

        @web.middleware
        async def guard(request: Any, handler: Any) -> Any:
            if not self._host_allowed(request):
                return web.Response(status=403, text="Open this page as http://127.0.0.1:%d" % self.port, headers=self.HEADERS)
            if not self._authorised(request):
                return web.Response(status=401, text="Password needed", headers={
                    "WWW-Authenticate": 'Basic realm="OwO Farmer"', **self.HEADERS})
            try:
                response = await handler(request)
            except web.HTTPException as exc:
                exc.headers.update(self.HEADERS)
                raise
            response.headers.update(self.HEADERS)
            return response

        async def index(_: Any) -> Any:
            return web.Response(text=INDEX_HTML, content_type="text/html")

        async def stats(_: Any) -> Any:
            return web.json_response(snapshot(self.farmer))

        async def nothing(_: Any) -> Any:
            return web.Response(status=204)

        app = web.Application(middlewares=[guard])
        app.router.add_get("/", index)
        app.router.add_get("/api/stats", stats)
        app.router.add_get("/favicon.ico", nothing)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, self.cfg.host, self.cfg.port).start()
        except OSError:
            await self._runner.cleanup()
            self._runner = None
            raise
        self.port = int(self._runner.addresses[0][1])
        shown = "localhost" if self.cfg.host in ("0.0.0.0", "::") else self.cfg.host
        self.url = f"http://{shown}:{self.port}"
        return self.url

    async def stop(self) -> None:
        if self._runner is not None:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None


class KeyListener(threading.Thread):
    def __init__(self, loop: asyncio.AbstractEventLoop, on_key: Callable[[str], None]):
        super().__init__(daemon=True)
        self.loop, self.on_key = loop, on_key
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                while not self._halt.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch in ("\x00", "\xe0"):
                            msvcrt.getwch()
                            continue
                        self.loop.call_soon_threadsafe(self.on_key, ch.lower())
                    else:
                        time.sleep(0.05)
            else:
                import select
                import termios
                import tty
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setcbreak(fd)
                    while not self._halt.is_set():
                        ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                        if ready:
                            self.loop.call_soon_threadsafe(self.on_key, sys.stdin.read(1).lower())
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:  # no terminal, odd console: hotkeys are optional
            pass


# ======================================================================================
# 9. Logging, running, command line
# ======================================================================================

class Redact(logging.Filter):
    TOKEN = re.compile(r"[\w-]{20,}\.[\w-]{5,}\.[\w-]{20,}")

    def __init__(self, secrets: List[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for secret in self.secrets:
            msg = msg.replace(secret, "[hidden]")
        record.msg, record.args = self.TOKEN.sub("[hidden]", msg), ()
        return True


def setup_logging(cfg: SimpleNamespace, config_dir: Path, secrets: List[str], console: bool) -> logging.Logger:
    logger = logging.getLogger("owo_farmer")
    logger.setLevel(getattr(logging, cfg.logging.level))
    logger.handlers.clear()
    logger.propagate = False
    redact = Redact(secrets)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers: List[logging.Handler] = []
    if cfg.logging.file:
        path = (config_dir / cfg.logging.file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    if console:
        handlers.append(logging.StreamHandler(sys.stdout))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(redact)
        logger.addHandler(h)
    return logger


def print_summary(f: Farmer, reason: str = "") -> None:
    s = f.stats
    lines = [
        f"\nOwO Farmer finished: {f.stop_reason or reason or 'stopped'}",
        f"  ran for {fmt_dur(f.clock() - f.started_at)}, sent {s.sent} messages",
        f"  hunts {s.hunts}, battles {s.battles}, prays {s.prays}, curses {s.curses}, dailies {s.dailies}",
        f"  captchas {s.captchas}, slow-downs {s.slowdowns}, unanswered {s.missed}, send errors {s.errors}",
    ]
    if s.cash is not None and s.cash_start is not None:
        lines.append(f"  cowoncy {s.cash_start:,} -> {s.cash:,} ({s.cash - s.cash_start:+,})")
    if f.gambler:
        g = f.gambler
        lines.append(f"  gambling: {g.bets} bets, {g.wins} won, {g.losses} lost, net {g.net:+,}")
    print("\n".join(lines))


def build_client(farmer: Farmer, late: LateTransport, cfg: SimpleNamespace, ready: asyncio.Event,
                 logger: logging.Logger) -> Any:
    """Create the Discord client. Imports discord lazily so the rest of the module works without it."""
    import discord

    class FarmClient(discord.Client):
        # Raw gateway events are the main source: they also carry the newer "components v2" messages
        # that discord.py-self cannot read. The normal message events are only a fallback.
        raw_ok = False

        async def on_ready(self) -> None:
            user = self.user
            if ready.is_set() or user is None:
                return
            try:
                channel = self.get_channel(farmer.channel_id) or await self.fetch_channel(farmer.channel_id)
            except discord.HTTPException as exc:
                farmer.stop(f"cannot open channel {farmer.channel_id}: {exc}", EXIT_CHANNEL, True)
                ready.set()
                return
            guild = getattr(channel, "guild", None)
            names = [user.name, getattr(user, "display_name", ""), getattr(user, "global_name", "")]
            if guild is not None and guild.me is not None:
                names.append(getattr(guild.me, "nick", "") or "")
                if not channel.permissions_for(guild.me).send_messages:
                    farmer.stop("this account cannot send messages in that channel", EXIT_CHANNEL, True)
                    ready.set()
                    return
            late.inner = DiscordTransport(channel, cfg.farm.silent)
            farmer.set_identity(user.id, names)
            farmer.start()
            ready.set()

        async def on_socket_raw_receive(self, frame: Any) -> None:
            if not ready.is_set() or self.user is None:
                return
            if isinstance(frame, (bytes, bytearray)):
                frame = bytes(frame).decode("utf-8", "replace")
            if not isinstance(frame, str) or ('"MESSAGE_CREATE"' not in frame and '"MESSAGE_UPDATE"' not in frame):
                return
            self.raw_ok = True
            if str(OWO_ID) not in frame and str(self.user.id) not in frame:
                return                              # nothing from OwO or from us: skip the JSON work
            try:
                payload = json.loads(frame)
                inc = incoming_from_payload(payload.get("d") or {}, self.user.id, payload.get("t") == "MESSAGE_UPDATE")
                if inc is not None:
                    await farmer.handle(inc)
            except Exception:
                logger.exception("error while handling a gateway message")

        async def on_message(self, message: Any) -> None:
            if not self.raw_ok:
                await self._feed(message, False)

        async def on_message_edit(self, before: Any, after: Any) -> None:
            if not self.raw_ok:
                await self._feed(after, True)

        async def _feed(self, message: Any, edited: bool) -> None:
            if not ready.is_set() or self.user is None:
                return
            try:
                await farmer.handle(to_incoming(message, self.user.id, edited))
            except Exception:
                logger.exception("error while handling a message")

    return FarmClient(enable_debug_events=True)


async def run_live(cfg: SimpleNamespace, token: str, alert_token: str, config_path: Path, plain: bool) -> int:
    import discord

    config_dir = config_path.parent
    secrets = [token, alert_token, cfg.alerts.webhook_url, cfg.dashboard.password, cfg.account.token, cfg.alerts.dm.token]
    use_ui = cfg.ui.terminal_dashboard and not plain and sys.stdout.isatty()
    if use_ui:
        try:
            import rich  # noqa: F401
        except ModuleNotFoundError:
            use_ui = False
    logger = setup_logging(cfg, config_dir, secrets, console=not use_ui)
    state = StateFile(config_dir / "state.json")
    late = LateTransport()
    holder: Dict[str, Any] = {}

    def alert_log(level: str, text: str) -> None:
        holder["farmer"].log(level, text)

    pinger = DmPinger(cfg.alerts.dm, alert_token, alert_log) if cfg.alerts.dm.enabled else None
    alerts = Alerts(cfg.alerts, alert_log, pinger)
    farmer = Farmer(cfg, late, state=state, alerts=alerts, logger=logger)
    holder["farmer"] = farmer
    done = asyncio.Event()
    farmer.on_finish = done.set
    ready = asyncio.Event()

    web: Optional[WebDashboard] = None
    if cfg.dashboard.enabled:
        web = WebDashboard(farmer, cfg.dashboard)
        try:
            farmer.web_url = await web.start()
            print(f"Web dashboard: {farmer.web_url}")
        except OSError as exc:
            logger.warning("web dashboard could not start: %s", exc)
            print(f"The web dashboard could not start ({exc}); carrying on without it.")
            web = None

    client = build_client(farmer, late, cfg, ready, logger)
    loop = asyncio.get_running_loop()

    def interrupt(*_: Any) -> None:
        loop.call_soon_threadsafe(farmer.request_stop, "interrupted (Ctrl+C)")

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, interrupt)

    def on_key(ch: str) -> None:
        if ch == "p":
            farmer.toggle_manual_pause()
        elif ch == "c":
            farmer.captcha_solved()
        elif ch == "g" and farmer.gambler:
            farmer.gambler.manual_off = not farmer.gambler.manual_off
        elif ch in ("q", "\x03"):
            farmer.request_stop("quit (q)")

    keys = None
    if sys.stdin.isatty():
        keys = KeyListener(loop, on_key)
        keys.start()

    login = asyncio.create_task(client.start(token))

    async def tick_loop() -> None:
        await ready.wait()
        while not farmer.stopped:
            try:
                await farmer.tick()
            except Exception:
                logger.exception("error in scheduler")
            await asyncio.sleep(0.25)

    async def ui_loop() -> None:
        from rich.live import Live
        await ready.wait()
        with Live(render_dashboard(farmer), refresh_per_second=2, screen=False) as live:
            while not farmer.stopped:
                live.update(render_dashboard(farmer))
                await asyncio.sleep(0.5)
            live.update(render_dashboard(farmer))

    tasks = [asyncio.create_task(tick_loop())]
    if use_ui:
        tasks.append(asyncio.create_task(ui_loop()))
    code = EXIT_OK
    waiter = asyncio.create_task(done.wait())
    try:
        finished, _ = await asyncio.wait({login, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if login in finished and not farmer.stopped:
            login_error = login.exception()
            if isinstance(login_error, discord.LoginFailure):
                print("Discord rejected the token. Copy a fresh one and try again.")
                code = EXIT_LOGIN
            elif login_error:
                print(f"Could not connect to Discord: {login_error}")
                code = EXIT_LOGIN
            farmer.stopped = True
        else:
            code = farmer.exit_code
    finally:
        if not farmer.stopped:
            farmer.stop(farmer.stop_reason or "closed")
        for t in tasks + [waiter]:
            t.cancel()
        if keys:
            keys.stop()
        with contextlib.suppress(Exception):
            await client.close()
        with contextlib.suppress(Exception):
            login.cancel()
        with contextlib.suppress(Exception):
            await alerts.drain()                 # let a final webhook / DM alert finish
        if pinger is not None:
            await pinger.close()
        if web is not None:
            await web.stop()
        state.save(farmer.clock(), force=True)
    if farmer.started_at:
        print_summary(farmer)
    return code


def print_check(cfg: SimpleNamespace, warnings: List[str]) -> None:
    print("Config OK.")
    for w in warnings:
        print(f"  note: {w}")
    print(f"  channel {cfg.farm.channel_id}, prefix '{cfg.farm.prefix}', gap {cfg.farm.gap[0]:g}-{cfg.farm.gap[1]:g}s")
    for name in ("owo", "hunt", "battle", "pray", "daily", "cookie", "sell", "sac", "lootbox", "crate", "cash"):
        c = getattr(cfg.commands, name)
        if c.enabled:
            cd = getattr(c, "cooldown", None)
            print(f"  {name:<8} every {cd[0]:g}-{cd[1]:g}s" if cd else f"  {name:<8} once a day")
    print(f"  gems: {'on' if cfg.gems.enabled else 'off'}   gamble: "
          f"{cfg.gamble.game + ' / ' + cfg.gamble.strategy if cfg.gamble.enabled else 'off'}   custom: {len(cfg.custom)}")
    for name in ("hunt", "battle"):
        c = getattr(cfg.commands, name)
        if c.enabled:
            print(f"  {name} timing: {c.instant_chance:.0%} of the time right after the cooldown, otherwise "
                  f"{c.delay[0]:g}-{c.delay[1]:g}s later")
    saf = cfg.safety
    print(f"  breaks: rest {saf.break_minutes:g} min after every {saf.break_every_minutes:g} min of running"
          if saf.break_every_minutes else "  breaks: off")
    dm = cfg.alerts.dm
    if dm.enabled:
        have = bool(os.environ.get(dm.token_env) or dm.token)
        print(f"  DM alerts: on, to user {dm.user_id}, every {dm.interval_seconds:g}s (max {dm.max_pings}); "
              f"alert-account token {'found' if have else 'MISSING'}")
    else:
        print("  DM alerts: off")
    dash = cfg.dashboard
    if dash.enabled:
        shown = "localhost" if dash.host in ("0.0.0.0", "::") else dash.host
        print(f"  web dashboard: http://{shown}:{dash.port}" + ("  (password protected)" if dash.password else ""))
    else:
        print("  web dashboard: off")
    problem = discord_library_problem()
    print("  discord.py-self: ready" if problem is None else f"  WARNING: {problem}")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="owo_farmer", description="Careful OwO grinder for a Discord account.")
    p.add_argument("-c", "--config", type=Path, default=HERE / "config.toml", help="config file (default: config.toml)")
    p.add_argument("--init", action="store_true", help="write a fresh config.toml and exit")
    p.add_argument("--force", action="store_true", help="with --init: overwrite an existing file")
    p.add_argument("--check", action="store_true", help="validate the config and exit (no login)")
    p.add_argument("--simulate", type=float, metavar="MINUTES", help="run against a fake OwO for N minutes, offline")
    p.add_argument("--sim-captcha", type=float, metavar="MINUTE", help="with --simulate: raise a captcha at this minute")
    p.add_argument("--seed", type=int, default=1, help="with --simulate: random seed")
    p.add_argument("--plain", action="store_true", help="plain log output instead of the dashboard")
    p.add_argument("--version", action="version", version=f"owo_farmer {__version__}")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = parse_args(argv)
    if args.init:
        if args.config.exists() and not args.force:
            print(f"{args.config} already exists (use --force to overwrite).")
            return EXIT_CONFIG
        args.config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        print(f"Wrote {args.config}. Set farm.channel_id and your token, then run:  python owo_farmer.py --check")
        return EXIT_OK
    try:
        cfg, warnings = load_config(args.config, require_channel=args.simulate is None)
    except ConfigError as exc:
        print("Config problems:")
        for issue in exc.problems:
            print(f"  - {issue}")
        return EXIT_CONFIG
    if args.check:
        print_check(cfg, warnings)
        return EXIT_OK
    if args.simulate is not None:
        farmer, sim = asyncio.run(run_simulation(cfg, args.simulate, args.seed, args.sim_captcha))
        print_summary(farmer, "simulation ended")
        print(f"  simulated OwO balance: {sim.cash:,}")
        t0 = farmer.started_at
        notable = [e for e in farmer.events if not e.text.startswith("sent:")]
        print("  notable events:" if notable else "  no warnings or pauses")
        for event in notable[-15:]:
            print(f"    +{fmt_dur(event.t - t0)} [{event.level}] {event.text}")
        return farmer.exit_code
    try:
        token = resolve_token(cfg)
        alert_token = resolve_alert_token(cfg, token) if cfg.alerts.dm.enabled else ""
    except ConfigError as exc:
        print("\n".join(exc.problems))
        return EXIT_CONFIG
    problem = discord_library_problem()
    if problem:
        print(problem)
        return EXIT_CONFIG
    for w in warnings:
        print(f"note: {w}")
    try:
        return asyncio.run(run_live(cfg, token, alert_token, args.config, args.plain))
    except KeyboardInterrupt:
        return EXIT_OK
    except ModuleNotFoundError as exc:
        print(f"Missing package: {exc.name}. Run:  pip install -r requirements.txt")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
