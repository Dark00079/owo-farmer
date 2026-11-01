"""Offline tests for owo_farmer. No Discord account or network needed.

    python -m unittest -v
"""

from __future__ import annotations

import asyncio
import copy
import datetime as dt
import io
import logging
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import owo_farmer as of

COW = "<:cowoncy:416043450337853441>"
# Noon in OwO's reset timezone (Pacific), so the next daily reset is 12 hours away on any machine.
START = dt.datetime(2026, 5, 28, 19, 0, 0, tzinfo=dt.timezone.utc).timestamp()

HUNT = f"**\U0001F331 | Tester** spent 5 {COW} and caught a **bunny** \U0001F430!"
HUNT_GEMS = ("**\U0001F331 | Tester**, hunt is empowered by <:cgem1:111>`[3/25]` <:ugem3:222>`[2/25]` !\n"
             "<:blank:427371936482328596> **|** You found: \U0001F430 \U0001F430 \U0001F426")
HUNT_ALL_GEMS = ("**\U0001F331 | Tester**, hunt is empowered by <:cgem1:111>`[3/25]` <:ugem3:222>`[2/25]` "
                 "<a:lgem4:333>`[1/25]` !\n<:blank:427371936482328596> **|** You found: \U0001F430")
CASH = f"{COW} **| Tester**, you currently have **__1,234__ cowoncy!**"
DAILY = (f"\U0001F4B0 **| Tester**, Here is your daily **{COW} 525 Cowoncy**!\n"
         "<:blank:427371936482328596> **|** You're on a **3 daily streak**!")
DAILY_WAIT = "**⏱ |** Nu! **Tester**! You need to wait **5H 38M 12S**"
COOKIE_WAIT = "**\U0001F36A | Tester**, Nu! You need to wait **2H 3M 4S**"
SLOW = "**⏱ | Tester**! Slow down and try the command again **<t:1780000100:R>**"
THROTTLE = "**\U0001F6AB | Tester**, Please slow down~ You're a little too fast for me :c"
BROKE = "**\U0001F6AB | Tester**, You don't have enough cowoncy!"
CAPTCHA_CH = "**⚠️ | Tester**, are you a real human? Please use the link below so I can check! (1/5)"
CAPTCHA_DM = "⚠️ **|** Please complete your captcha to verify that you are human! (1/5)"
CAPTCHA_BANNED_WORD = "⚠️ **|** Please complete your captcha within 10 minutes or you will be banned! (2/5)"
VERIFIED = "**✅ |** I have verified that you are human! Thank you! :3"
BAN = "**☠ |** You have been banned for macros. Please contact support."
CF_SPIN = f"**Tester** spent **{COW} 400** and chose **heads**\nThe coin spins... <a:coinflip:436677458339823636>"
CF_WIN = (f"**Tester** spent **{COW} 400** and chose **heads**\n"
          f"The coin spins... <:head:436677933977960478> and you won **{COW} 800**!!")
CF_LOSE = (f"**Tester** spent **{COW} 400** and chose **heads**\n"
           "The coin spins... <:tail:436677926398853120> and you lost it all... :c")
SLOTS_SPIN = f"**\U0001F3B0 | Tester** bet {COW} 400\n`___SLOTS___`\n  `| <a:slots:1> |`"
SLOTS_WIN = (f"**\U0001F3B0 | Tester** bet {COW} 400\n`___SLOTS___`\n"
             f"  `| \U0001F352 \U0001F352 \U0001F352 |`   and won {COW} 1,200")
SLOTS_LOSE = (f"**\U0001F3B0 | Tester** bet {COW} 400\n`___SLOTS___`\n"
              "  `| \U0001F352 \U0001F34B \U0001F346 |`   and won nothing... :c")
INVENTORY = ("**\U0001F4E6 | Tester's Inventory**\n"
             "`051`<:cgem1:1>⁰³  `065`<:cgem3:2>⁰¹  `072`<:cgem4:3>¹²\n`100`<:box:4>⁰⁵")
GEM_USED = "**<:cgem1:1> | Tester**, you activated a(n) **Common Hunting Gem**!"
GEM_REFUSED = "**\U0001F6AB |** you already have an active Hunting gem or you do not own this gem!"
SOLD = f"**\U0001F52A | Tester** sold **\U0001F430x5 \U0001F426x2** for a total of **{COW} 1,050**"


def deep_update(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def cfg_from(overrides: Optional[Dict[str, Any]] = None) -> SimpleNamespace:
    # Scheduled breaks are off unless a test asks for them, so timing tests are not interrupted.
    raw: Dict[str, Any] = {"farm": {"channel_id": 1}, "safety": {"break_every_minutes": 0.0, "break_minutes": 0.0}}
    deep_update(raw, copy.deepcopy(overrides or {}))
    return of.build_config(raw)[0]


def incoming(text: str, *, mid: int = 1, edited: bool = False, dm: bool = False, author: int = of.OWO_ID,
             channel: Optional[int] = None, from_me: bool = False) -> of.Incoming:
    cid = channel if channel is not None else (0 if dm else 1)
    return of.Incoming(mid, cid, author, text, is_dm=dm, edited=edited, from_me=from_me)


def basic(user: str, password: str) -> Dict[str, str]:
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def flatten(d: Dict[str, Any], prefix: str = "") -> set:
    out = set()
    for key, value in d.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= flatten(value, path)
        else:
            out.add(path)
    return out


class Harness:
    """A farmer wired to a fake clock and a fake OwO, stepped by hand."""

    def __init__(self, overrides: Optional[Dict[str, Any]] = None, seed: int = 1, cash: int = 5000,
                 start: float = START, reply: bool = True):
        self.cfg = cfg_from(overrides)
        self.clock = of.SimClock(start)
        self.rng = random.Random(seed)
        self.transport = of.SimTransport(self.clock)
        self.alerts = of.NullAlerts()
        self.state = of.StateFile()
        self.farmer = of.Farmer(self.cfg, self.transport, clock=self.clock, rng=self.rng,
                                state=self.state, alerts=self.alerts)
        self.sim = of.SimOwO(self.farmer, self.clock, self.rng, "Tester", cash=cash, channel_id=self.cfg.farm.channel_id)
        if reply:
            self.transport.sim = self.sim
        self.farmer.set_identity(1, ["Tester"])
        self.farmer.start()

    async def run(self, seconds: float, step: float = 0.25) -> None:
        end = self.clock() + seconds
        while self.clock() < end and not self.farmer.stopped:
            self.clock.advance(step)
            await self.sim.deliver()
            await self.farmer.tick()

    async def run_until(self, cond: Callable[[], bool], limit: float = 600, step: float = 0.25) -> bool:
        end = self.clock() + limit
        while self.clock() < end and not self.farmer.stopped:
            if cond():
                return True
            self.clock.advance(step)
            await self.sim.deliver()
            await self.farmer.tick()
        return cond()

    def texts(self) -> List[str]:
        return [t for _, t in self.transport.sent]

    def times(self, text: str) -> List[float]:
        return [at for at, t in self.transport.sent if t == text]


# --------------------------------------------------------------------------------------
class TestText(unittest.TestCase):
    def test_normalize_folds_and_strips(self):
        raw = "\U0001d421\U0001d42e​nt  <:cowoncy:416043450337853441>   It’s"
        self.assertEqual(of.normalize(raw), "hunt it's")

    def test_squash_keeps_letters_and_digits(self):
        self.assertEqual(of.squash("**\U0001F331 | Te_st User1**"), "testuser1")

    def test_parse_duration(self):
        self.assertEqual(of.parse_duration("5h 38m 12s"), 5 * 3600 + 38 * 60 + 12)
        self.assertEqual(of.parse_duration("**1d 2h**"), 26 * 3600)
        self.assertEqual(of.parse_duration("45S"), 45)
        self.assertIsNone(of.parse_duration("soon"))


class TestParse(unittest.TestCase):
    def test_plain_hunt(self):
        f = of.parse_facts(HUNT)
        self.assertTrue(f.hunt)
        self.assertEqual(f.gems_active, frozenset())

    def test_hunt_with_gems(self):
        f = of.parse_facts(HUNT_GEMS)
        self.assertTrue(f.hunt)
        self.assertEqual(f.gems_active, frozenset({"hunting", "empowering"}))
        self.assertEqual(of.parse_facts(HUNT_ALL_GEMS).gems_active, frozenset({"hunting", "empowering", "lucky"}))

    def test_cash_and_daily(self):
        self.assertEqual(of.parse_facts(CASH).cash, 1234)
        self.assertEqual(of.parse_facts(DAILY).daily_ok, 525)
        self.assertEqual(of.parse_facts(DAILY_WAIT).wait, 5 * 3600 + 38 * 60 + 12)
        self.assertEqual(of.parse_facts(COOKIE_WAIT).wait, 2 * 3600 + 3 * 60 + 4)

    def test_slowdown_and_throttle(self):
        f = of.parse_facts(SLOW)
        self.assertTrue(f.cooldown)
        self.assertEqual(f.cooldown_until, 1780000100.0)
        self.assertTrue(of.parse_facts(THROTTLE).throttled)
        self.assertTrue(of.parse_facts(BROKE).no_cowoncy)

    def test_captcha_variants(self):
        for text in (CAPTCHA_CH, CAPTCHA_DM, CAPTCHA_BANNED_WORD):
            f = of.parse_facts(text)
            self.assertTrue(f.captcha, text)
            self.assertFalse(f.ban, text)
        self.assertTrue(of.parse_facts("", verify_button=True).captcha)
        self.assertTrue(of.parse_facts("⚠️", warn_attachment=True).captcha)

    def test_verified_and_ban(self):
        self.assertTrue(of.parse_facts(VERIFIED).verified)
        self.assertTrue(of.parse_facts(BAN).ban)

    def test_coinflip(self):
        self.assertIsNone(of.parse_facts(CF_SPIN).cf)
        self.assertEqual(of.parse_facts(CF_WIN).cf, (400, 800))
        self.assertEqual(of.parse_facts(CF_LOSE).cf, (400, 0))

    def test_slots(self):
        self.assertIsNone(of.parse_facts(SLOTS_SPIN).slots)
        self.assertEqual(of.parse_facts(SLOTS_WIN).slots, (400, 1200))
        self.assertEqual(of.parse_facts(SLOTS_LOSE).slots, (400, 0))

    def test_inventory_and_gem_replies(self):
        self.assertEqual(of.parse_facts(INVENTORY).inventory, {"051": 3, "065": 1, "072": 12, "100": 5})
        self.assertTrue(of.parse_facts(GEM_USED).gem_used)
        self.assertTrue(of.parse_facts(GEM_REFUSED).gem_refused)

    def test_sold_battle_pray(self):
        self.assertEqual(of.parse_facts(SOLD).sold, 1050)
        self.assertTrue(of.parse_facts("**⚔ | Tester** goes into battle!").battle)
        self.assertTrue(of.parse_facts("**\U0001F64F | Tester** prays... You feel lucky!").pray)
        self.assertTrue(of.parse_facts("**\U0001F47B | Tester** is now cursed. You feel unlucky...").curse)

    def test_ordinary_chat_is_ignored(self):
        f = of.parse_facts("hello there, how are you?")
        self.assertEqual(f, of.Facts())


class TestConfig(unittest.TestCase):
    def test_defaults_are_valid_once_channel_is_set(self):
        cfg, warnings = of.build_config({"farm": {"channel_id": 5}})
        self.assertEqual(warnings, [])
        self.assertTrue(cfg.commands.hunt.enabled)

    def test_template_documents_every_option(self):
        parsed = of.tomllib.loads(of.CONFIG_TEMPLATE)
        self.assertEqual(flatten(of.DEFAULTS) - {"custom"}, flatten(parsed) - {"custom"})
        parsed["farm"]["channel_id"] = 7
        cfg, warnings = of.build_config(parsed)
        self.assertEqual(warnings, [])
        self.assertEqual(cfg.farm.channel_id, 7)

    def test_template_values_equal_defaults(self):
        parsed = of.tomllib.loads(of.CONFIG_TEMPLATE)
        parsed["farm"]["channel_id"] = 0
        from_template = of.build_config(parsed, require_channel=False)[0]
        from_defaults = of.build_config({}, require_channel=False)[0]
        self.assertEqual(vars(from_template.farm), vars(from_defaults.farm))
        self.assertEqual(vars(from_template.gamble), vars(from_defaults.gamble))
        self.assertEqual(vars(from_template.safety), vars(from_defaults.safety))

    def test_channel_is_required(self):
        with self.assertRaises(of.ConfigError) as ctx:
            of.build_config({})
        self.assertIn("channel_id", str(ctx.exception))
        of.build_config({}, require_channel=False)

    def test_unknown_option_suggests_fix(self):
        with self.assertRaises(of.ConfigError) as ctx:
            of.build_config({"farm": {"channel_id": 1}, "commands": {"hunt": {"cooldwon": [16, 17]}}})
        self.assertIn("did you mean 'cooldown'", str(ctx.exception))

    def test_type_errors_are_reported_together(self):
        with self.assertRaises(of.ConfigError) as ctx:
            of.build_config({"farm": {"channel_id": "abc", "prefix": 5, "gap": [5, 1]}})
        self.assertEqual(len(ctx.exception.problems), 3)

    def test_cooldowns_below_owo_limit_are_raised(self):
        cfg, warnings = of.build_config({"farm": {"channel_id": 1}, "commands": {"hunt": {"cooldown": [1, 2]}}})
        self.assertEqual(cfg.commands.hunt.cooldown, (15.5, 16.0))
        self.assertTrue(any("commands.hunt.cooldown" in w for w in warnings))

    def test_gap_floor(self):
        cfg, warnings = of.build_config({"farm": {"channel_id": 1, "gap": [0.1, 0.2]}})
        self.assertGreaterEqual(cfg.farm.gap[0], 2.0)
        self.assertTrue(warnings)

    def test_enabled_features_need_their_arguments(self):
        for section, expect in (("sell", "args"), ("sac", "args"), ("cookie", "target")):
            with self.assertRaises(of.ConfigError) as ctx:
                of.build_config({"farm": {"channel_id": 1}, "commands": {section: {"enabled": True}}})
            self.assertIn(expect, str(ctx.exception))

    def test_gamble_validation(self):
        bad = (({"game": "dice"}, "gamble.game"), ({"strategy": "martingale", "multiplier": 1.0}, "multiplier"),
               ({"max_bet": 10, "base_bet": 100}, "max_bet"), ({"max_balance_fraction": 0}, "max_balance_fraction"))
        for override, expect in bad:
            with self.assertRaises(of.ConfigError) as ctx:
                of.build_config({"farm": {"channel_id": 1}, "gamble": override})
            self.assertIn(expect, str(ctx.exception))
        cfg, _ = of.build_config({"farm": {"channel_id": 1}, "gamble": {"max_bet": 10 ** 7}})
        self.assertEqual(cfg.gamble.max_bet, of.OWO_MAX_BET)

    def test_gambling_switches_balance_tracking_on(self):
        cfg, warnings = of.build_config({"farm": {"channel_id": 1}, "gamble": {"enabled": True},
                                         "commands": {"cash": {"enabled": False}}})
        self.assertTrue(cfg.commands.cash.enabled)
        self.assertTrue(any("balance" in w for w in warnings))

    def test_webhook_must_be_a_discord_webhook(self):
        with self.assertRaises(of.ConfigError):
            of.build_config({"farm": {"channel_id": 1}, "alerts": {"webhook_url": "https://evil.example/hook"}})
        cfg, _ = of.build_config({"farm": {"channel_id": 1},
                                  "alerts": {"webhook_url": "https://discord.com/api/webhooks/123/abc-DEF_1"}})
        self.assertTrue(cfg.alerts.webhook_url)

    def test_quiet_hours(self):
        cfg, _ = of.build_config({"farm": {"channel_id": 1}, "safety": {"quiet_hours": ["22:00-06:30", "13:00-14:00"]}})
        self.assertEqual(cfg.safety.quiet_windows, [(1320, 390), (780, 840)])
        with self.assertRaises(of.ConfigError):
            of.build_config({"farm": {"channel_id": 1}, "safety": {"quiet_hours": ["25:00-26:00"]}})

    def test_in_quiet_handles_midnight_wrap(self):
        windows = [(22 * 60, 6 * 60 + 30)]
        self.assertTrue(of.in_quiet(23 * 60, windows))
        self.assertTrue(of.in_quiet(2 * 60, windows))
        self.assertFalse(of.in_quiet(6 * 60 + 30, windows))
        self.assertFalse(of.in_quiet(12 * 60, windows))
        self.assertFalse(of.in_quiet(5, [(60, 60)]))

    def test_custom_commands(self):
        cfg, warnings = of.build_config({"farm": {"channel_id": 1}, "custom": [{"text": "zoo", "cooldown": [1, 2]}]})
        self.assertEqual(cfg.custom[0].cooldown[0], 5.0)
        self.assertTrue(warnings)
        with self.assertRaises(of.ConfigError):
            of.build_config({"farm": {"channel_id": 1}, "custom": [{"cooldown": [10, 20]}]})

    def test_token_resolution(self):
        cfg = cfg_from({"account": {"token_env": "OWO_TEST_TOKEN_VAR", "token": "from-config"}})
        os.environ.pop("OWO_TEST_TOKEN_VAR", None)
        self.assertEqual(of.resolve_token(cfg), "from-config")
        os.environ["OWO_TEST_TOKEN_VAR"] = "from-env"
        try:
            self.assertEqual(of.resolve_token(cfg), "from-env")
            os.environ["OWO_TEST_TOKEN_VAR"] = "has space"
            with self.assertRaises(of.ConfigError):
                of.resolve_token(cfg)
        finally:
            os.environ.pop("OWO_TEST_TOKEN_VAR", None)
        with self.assertRaises(of.ConfigError):
            of.resolve_token(cfg_from({"account": {"token_env": "OWO_TEST_TOKEN_VAR", "token": ""}}))


class TestGambler(unittest.TestCase):
    NOW = START

    def gambler(self, **over: Any) -> of.Gambler:
        base = {"enabled": True, "stop_loss": 0, "take_profit": 0, "daily_loss_limit": 0, "keep_balance": 0,
                "max_balance_fraction": 1.0}
        base.update(over)
        return of.Gambler(cfg_from({"gamble": base}).gamble, random.Random(1), of.StateFile())

    def play(self, g: of.Gambler, win: bool, cash: Optional[int] = None) -> Optional[int]:
        bet = g.next_bet(cash, self.NOW)
        if bet is None:
            return None
        g.on_sent(bet, self.NOW)
        g.on_result(bet, bet * 2 if win else 0, self.NOW)
        return bet

    def test_flat_bets_never_change(self):
        g = self.gambler(base_bet=100)
        self.assertEqual([self.play(g, w) for w in (False, False, True, False)], [100] * 4)

    def test_martingale_doubles_then_resets_at_max_streak(self):
        g = self.gambler(strategy="martingale", base_bet=100, multiplier=2.0, max_streak=5, max_bet=10_000)
        bets = [self.play(g, False) for _ in range(7)]
        self.assertEqual(bets, [100, 200, 400, 800, 1600, 100, 200])

    def test_martingale_returns_to_base_after_a_win(self):
        g = self.gambler(strategy="martingale", base_bet=100, max_streak=0)
        self.assertEqual([self.play(g, w) for w in (False, False, True, False)], [100, 200, 400, 100])

    def test_max_bet_caps_growth(self):
        g = self.gambler(strategy="martingale", base_bet=1000, multiplier=3.0, max_bet=2500, max_streak=0)
        self.assertEqual([self.play(g, False) for _ in range(3)], [1000, 2500, 2500])

    def test_stop_loss_halts_and_stays_halted(self):
        g = self.gambler(base_bet=100, stop_loss=300)
        self.assertEqual([self.play(g, False) for _ in range(3)], [100, 100, 100])
        self.assertIsNone(g.next_bet(None, self.NOW))
        self.assertIn("stop-loss", g.halt_reason)
        self.assertIsNone(g.next_bet(None, self.NOW + 10_000))

    def test_last_bet_is_trimmed_to_the_loss_budget(self):
        g = self.gambler(base_bet=100, stop_loss=250)
        self.assertEqual([self.play(g, False) for _ in range(3)], [100, 100, 50])
        self.assertIsNone(g.next_bet(None, self.NOW))
        self.assertEqual(g.net, -250)

    def test_take_profit_halts(self):
        g = self.gambler(base_bet=100, take_profit=200)
        self.assertEqual([self.play(g, True), self.play(g, True)], [100, 100])
        self.assertIsNone(g.next_bet(None, self.NOW))
        self.assertIn("take-profit", g.halt_reason)

    def test_daily_loss_limit_survives_restarts(self):
        state = of.StateFile()
        today = dt.date.fromtimestamp(self.NOW).isoformat()
        state.set(gamble_date=today, gamble_net=-4950)
        cfg = cfg_from({"gamble": {"enabled": True, "stop_loss": 0, "daily_loss_limit": 5000, "keep_balance": 0,
                                   "max_balance_fraction": 1.0, "base_bet": 100}})
        g = of.Gambler(cfg.gamble, random.Random(1), state)
        self.assertEqual(g.next_bet(None, self.NOW), 50)
        g.on_sent(50, self.NOW)
        g.on_result(50, 0, self.NOW)
        self.assertIsNone(g.next_bet(None, self.NOW))
        self.assertIn("daily loss", g.halt_reason)

    def test_daily_counter_resets_on_a_new_day(self):
        state = of.StateFile()
        state.set(gamble_date="2000-01-01", gamble_net=-999)
        g = of.Gambler(cfg_from({"gamble": {"enabled": True}}).gamble, random.Random(1), state)
        self.assertEqual(g.net_today(self.NOW), 0)

    def test_balance_limits_bet_size(self):
        g = self.gambler(base_bet=500, max_bet=500, max_balance_fraction=0.1, keep_balance=0)
        self.assertEqual(g.next_bet(1000, self.NOW), 100)
        g = self.gambler(base_bet=500, max_bet=500, max_balance_fraction=1.0, keep_balance=800)
        self.assertEqual(g.next_bet(1000, self.NOW), 200)

    def test_too_poor_to_bet_pauses_then_resumes(self):
        g = self.gambler(base_bet=100, keep_balance=500, broke_pause_minutes=10)
        self.assertIsNone(g.next_bet(400, self.NOW))
        self.assertEqual(g.halt_reason, "balance too low")
        self.assertFalse(g.active(self.NOW + 300))
        self.assertTrue(g.active(self.NOW + 601))
        self.assertEqual(g.next_bet(5000, self.NOW + 601), 100)

    def test_results_for_other_bets_are_ignored(self):
        g = self.gambler(base_bet=100)
        self.assertIsNone(g.on_result(100, 200, self.NOW))
        g.on_sent(100, self.NOW)
        self.assertIsNone(g.on_result(400, 0, self.NOW))
        self.assertIsNotNone(g.pending)
        self.assertEqual(g.on_result(100, 100, self.NOW), 0)
        self.assertEqual((g.pushes, g.wins, g.losses), (1, 0, 0))

    def test_pending_bet_expires(self):
        g = self.gambler()
        g.on_sent(100, self.NOW)
        self.assertFalse(g.expire(self.NOW + 10))
        self.assertTrue(g.expire(self.NOW + 60))
        self.assertIsNone(g.pending)

    def test_max_bets(self):
        g = self.gambler(max_bets=2)
        self.assertEqual([self.play(g, True), self.play(g, True), self.play(g, True)], [100, 100, None])


class TestGems(unittest.TestCase):
    def manager(self, **over: Any) -> of.GemManager:
        return of.GemManager(cfg_from({"gems": dict({"enabled": True}, **over)}).gems)

    def test_ids(self):
        self.assertEqual(of.gem_id("hunting", "common"), "051")
        self.assertEqual(of.gem_id("empowering", "fabled"), "071")
        self.assertEqual(of.gem_id("lucky", "rare"), "074")
        self.assertEqual(of.gem_id("special", "fabled"), "085")

    def test_missing_types_start_a_check_once(self):
        g = self.manager()
        self.assertFalse(g.on_hunt(frozenset({"hunting", "empowering", "lucky"}), 1000))
        self.assertTrue(g.on_hunt(frozenset({"hunting"}), 1000))
        self.assertEqual(g.missing, ["empowering", "lucky"])
        self.assertFalse(g.on_hunt(frozenset({"hunting"}), 1001))

    def test_lowest_tier_first_and_zero_counts_skipped(self):
        g = self.manager()
        g.on_hunt(frozenset({"hunting"}), 1000)
        self.assertTrue(g.on_inventory({"065": 2, "067": 1, "072": 0, "074": 5}, 1001))
        self.assertEqual(g.use_ids, ["065", "074"])
        self.assertEqual(g.use_text(), "use 65 74")

    def test_highest_tier_first(self):
        g = self.manager(lowest_first=False)
        g.on_hunt(frozenset({"hunting"}), 1000)
        g.on_inventory({"065": 2, "067": 1, "074": 5}, 1001)
        self.assertEqual(g.use_ids, ["067", "074"])

    def test_tier_filter(self):
        g = self.manager(tiers=["rare"])
        g.on_hunt(frozenset({"hunting"}), 1000)
        g.on_inventory({"065": 2, "067": 1, "072": 9, "074": 5}, 1001)
        self.assertEqual(g.use_ids, ["067", "074"])

    def test_no_gems_waits_before_asking_again(self):
        g = self.manager(empty_retry_minutes=30)
        g.on_hunt(frozenset(), 1000)
        self.assertFalse(g.on_inventory({}, 1001))
        self.assertEqual(g.state, "idle")
        self.assertFalse(g.on_hunt(frozenset(), 1000 + 600))
        self.assertTrue(g.on_hunt(frozenset(), 1001 + 1800 + 200))

    def test_repeated_failures_back_off(self):
        g = self.manager(check_cooldown=100)
        now, starts = 1000.0, []
        for _ in range(4):
            while not g.on_hunt(frozenset(), now):     # first moment a new check is allowed
                now += 10
            starts.append(now)
            g.on_inventory({"051": 1}, now + 1)        # a gem was used, but the next hunt still lacks it
            g.done()
        self.assertEqual([b - a for a, b in zip(starts, starts[1:])], [200, 400, 800])

    def test_stuck_state_expires(self):
        g = self.manager()
        g.on_hunt(frozenset(), 1000)
        g.expire(1030)
        self.assertEqual(g.state, "inventory")
        g.expire(1100)
        self.assertEqual(g.state, "idle")


class TestFarmer(unittest.IsolatedAsyncioTestCase):
    async def test_long_run_keeps_every_cooldown_and_cap(self):
        h = await self._run_default(3 * 3600)
        sent = h.transport.sent
        self.assertGreater(len(sent), 800)
        gaps = [b[0] - a[0] for a, b in zip(sent, sent[1:])]
        self.assertGreaterEqual(min(gaps), h.cfg.farm.gap[0])
        for text, floor in (("owo h", 15.5), ("owo b", 15.5), ("owo", 10.5), ("owo pray", 305.0)):
            times = h.times(text)
            self.assertGreater(len(times), 1, text)
            self.assertGreaterEqual(min(b - a for a, b in zip(times, times[1:])), floor, text)
        window = [t for t, _ in sent]
        for i, t in enumerate(window):
            in_minute = sum(1 for u in window[i:i + 40] if u - t <= 60)
            self.assertLessEqual(in_minute, h.cfg.farm.max_messages_per_minute)
        s = h.farmer.stats
        self.assertEqual((s.slowdowns, s.missed, s.errors), (0, 0, 0))
        self.assertEqual(s.dailies, 1)
        self.assertIsNotNone(s.cash)

    async def _run_default(self, seconds: float, **kw: Any) -> Harness:
        h = Harness(**kw)
        await h.run(seconds)
        return h

    async def test_daily_is_claimed_again_after_the_reset(self):
        h = Harness(overrides={"commands": {"owo": {"enabled": False}, "battle": {"enabled": False},
                                            "hunt": {"enabled": False}, "pray": {"enabled": False}}})
        await h.run(30 * 3600, step=1.0)
        self.assertEqual(h.farmer.stats.dailies, 2)
        self.assertEqual(h.farmer.stats.slowdowns, 0)
        self.assertGreater(h.state.data["daily_next"], h.clock())

    async def test_same_seed_same_schedule(self):
        a, b, c = Harness(seed=7), Harness(seed=7), Harness(seed=8)
        for h in (a, b, c):
            await h.run(900)
        self.assertEqual(a.transport.sent, b.transport.sent)
        self.assertNotEqual(a.transport.sent, c.transport.sent)

    async def test_captcha_pauses_alerts_and_resumes_after_verification(self):
        h = Harness()
        await h.run(120)
        h.sim.trigger_captcha()
        await h.run(5)
        self.assertEqual(h.farmer.status, "paused")
        sent_at_pause = len(h.transport.sent)
        await h.run(300)
        self.assertEqual(len(h.transport.sent), sent_at_pause)
        self.assertEqual(h.farmer.stats.captchas, 1)
        self.assertTrue(any(kind == "captcha" for kind, _, _ in h.alerts.sent))
        h.sim.solve_captcha()
        await h.run(2)
        self.assertEqual(h.farmer.status, "paused")
        await h.run(75)
        self.assertEqual(h.farmer.status, "running")
        await h.run(60)
        self.assertGreater(len(h.transport.sent), sent_at_pause)

    async def test_a_second_captcha_notice_is_not_counted_twice(self):
        h = Harness()
        await h.run(60)
        h.sim.trigger_captcha()
        await h.run(3)
        await h.farmer.handle(incoming(CAPTCHA_CH, mid=900))
        self.assertEqual(h.farmer.stats.captchas, 1)

    async def test_unsolved_captcha_ends_the_session(self):
        h = Harness({"safety": {"captcha_give_up_minutes": 1.0}})
        await h.run(30)
        h.sim.trigger_captcha()
        await h.run(120)
        self.assertTrue(h.farmer.stopped)
        self.assertEqual(h.farmer.exit_code, of.EXIT_SAFETY)

    async def test_captcha_addressed_to_someone_else_only_pauses_briefly(self):
        h = Harness()
        await h.run(60)
        await h.farmer.handle(incoming("**⚠️ | Someone**, are you a real human? Please use the link below"))
        self.assertEqual(h.farmer.status, "paused")
        self.assertEqual(h.farmer.stats.captchas, 0)
        await h.run(200)
        self.assertEqual(h.farmer.status, "running")
        self.assertFalse(h.farmer.stopped)

    async def test_captcha_dm_is_always_for_us(self):
        h = Harness()
        await h.run(60)
        await h.farmer.handle(incoming(CAPTCHA_DM, dm=True))
        self.assertEqual(h.farmer.stats.captchas, 1)
        self.assertEqual(h.farmer.status, "paused")

    async def test_ban_stops_for_good(self):
        h = Harness()
        await h.run(60)
        await h.farmer.handle(incoming(BAN, dm=True))
        self.assertTrue(h.farmer.stopped)
        self.assertEqual(h.farmer.exit_code, of.EXIT_SAFETY)
        sent = len(h.transport.sent)
        await h.run(60)
        self.assertEqual(len(h.transport.sent), sent)

    async def test_names_that_cannot_be_matched_fall_back_to_the_channel(self):
        h = Harness()
        h.farmer.set_identity(1, ["\U0001F600", "x"])                 # emoji-only and one-letter names match nothing
        self.assertEqual(h.farmer.names, [])
        self.assertTrue(h.farmer.trust_channel)
        self.assertTrue(any("farm.extra_names" in e.text for e in h.farmer.events))
        await h.farmer.handle(incoming(HUNT.replace("Tester", "Whoever"), mid=70))
        self.assertEqual(h.farmer.stats.hunts, 1)
        await h.farmer.handle(incoming(HUNT, mid=71, channel=999))   # still only the farm channel
        self.assertEqual(h.farmer.stats.hunts, 1)

    async def test_extra_names_and_explicit_trust(self):
        h = Harness({"farm": {"extra_names": ["Old Nick"]}})
        h.farmer.set_identity(1, ["Tester"])
        self.assertIn("oldnick", h.farmer.names)
        self.assertFalse(h.farmer.trust_channel)
        await h.farmer.handle(incoming(HUNT.replace("Tester", "Old Nick"), mid=72))
        self.assertEqual(h.farmer.stats.hunts, 1)
        t = Harness({"farm": {"trust_channel": True}})
        await t.farmer.handle(incoming(HUNT.replace("Tester", "Anyone"), mid=73))
        self.assertEqual(t.farmer.stats.hunts, 1)
        with self.assertRaises(of.ConfigError):
            cfg_from({"farm": {"extra_names": [5]}})

    async def test_messages_from_others_are_ignored(self):
        h = Harness()
        await h.run(60)
        await h.farmer.handle(incoming(BAN, author=12345))
        await h.farmer.handle(incoming(CAPTCHA_CH, channel=999))
        await h.farmer.handle(incoming(HUNT.replace("Tester", "Somebody"), mid=5))
        self.assertFalse(h.farmer.stopped)
        self.assertEqual(h.farmer.status, "running")
        hunts = h.farmer.stats.hunts
        await h.farmer.handle(incoming(HUNT, mid=6))
        self.assertEqual(h.farmer.stats.hunts, hunts + 1)

    async def test_silent_owo_pauses_then_gives_up(self):
        h = Harness(reply=False)
        self.assertTrue(await h.run_until(lambda: "no_reply" in h.farmer.pauses, 400))
        self.assertTrue(any(kind == "no_reply" for kind, _, _ in h.alerts.sent))
        await h.run(3000)
        self.assertTrue(h.farmer.stopped)
        self.assertEqual(h.farmer.exit_code, of.EXIT_SAFETY)

    async def test_a_command_with_no_reply_is_not_blamed_while_owo_is_alive(self):
        h = Harness({"custom": [{"text": "zoo", "cooldown": [60, 70], "expects_reply": True}]})
        await h.run(3600)                                  # the simulator never answers "zoo"
        self.assertGreater(h.farmer.jobs["custom0"].sent, 10)
        self.assertEqual(h.farmer.stats.missed, 0)
        self.assertNotIn("no_reply", h.farmer.pauses)

    async def test_reply_attribution(self):
        f = Harness(reply=False).farmer
        f.awaiting.extend([of.Awaiting("battle", 100.0), of.Awaiting("hunt", 195.0)])
        self.assertEqual(f._on_reply(200.0), "hunt")       # the stale battle entry is dropped quietly
        self.assertEqual(len(f.awaiting), 0)
        f.awaiting.extend([of.Awaiting("hunt", 197.0), of.Awaiting("battle", 199.0)])
        self.assertEqual(f._on_reply(200.0), "hunt")       # two in flight: answers arrive in order
        self.assertEqual([a.job for a in f.awaiting], ["battle"])
        self.assertIsNone(of.Farmer._on_reply(f, 500.0))   # nothing in flight: no culprit

    async def test_running_out_of_cowoncy_backs_hunting_off_but_battles_carry_on(self):
        h = Harness({"commands": {"daily": {"enabled": False}, "cash": {"enabled": False}}}, cash=0)
        await h.run(300)
        self.assertLessEqual(h.texts().count("owo h"), 1)
        self.assertGreaterEqual(h.texts().count("owo b"), 8)

    async def test_a_reply_clears_the_miss_counter(self):
        h = Harness()
        await h.run(300)
        self.assertEqual(h.farmer.missed, 0)
        self.assertEqual(h.farmer.no_reply_pauses, 0)

    async def test_repeated_slowdown_notices_pause_everything(self):
        h = Harness()
        await h.run(60)
        for i in range(h.cfg.safety.slowdown_limit):
            await h.farmer.handle(incoming(SLOW, mid=700 + i))
        self.assertIn("slowdown", h.farmer.pauses)
        sent = len(h.transport.sent)
        await h.run(60)
        self.assertEqual(len(h.transport.sent), sent)
        await h.run(400)
        self.assertNotIn("slowdown", h.farmer.pauses)

    async def test_cooldown_notice_pushes_back_the_command_that_caused_it(self):
        h = Harness(reply=False)
        self.assertTrue(await h.run_until(lambda: h.farmer.jobs["hunt"].sent >= 1, 120))
        h.farmer.awaiting.clear()
        h.farmer.awaiting.append(of.Awaiting("hunt", h.clock()))
        before = h.farmer.jobs["hunt"].next_due
        text = SLOW.replace("1780000100", str(int(h.clock() + 40)))
        await h.farmer.handle(incoming(text, mid=800))
        self.assertGreater(h.farmer.jobs["hunt"].next_due, max(before, h.clock() + 40))
        self.assertEqual(h.farmer.stats.slowdowns, 1)

    async def test_throttle_notice_backs_off(self):
        h = Harness()
        await h.run(60)
        now = h.clock()
        await h.farmer.handle(incoming(THROTTLE, mid=801))
        self.assertGreaterEqual(h.farmer.hold_until, now + 8)
        self.assertEqual(h.farmer.boost, 2.0)

    async def test_manual_commands_reset_timers_but_our_own_echo_does_not(self):
        h = Harness()
        self.assertTrue(await h.run_until(lambda: h.farmer.jobs["hunt"].sent >= 1, 120))
        echo = h.texts()[[t for t in h.texts()].index("owo h")]
        await h.farmer.handle(incoming(echo, from_me=True, mid=50))
        self.assertEqual(h.farmer.stats.manual, 0)
        job = h.farmer.jobs["hunt"]
        job.next_due = h.clock()
        await h.farmer.handle(incoming("owo hunt", from_me=True, mid=51))
        self.assertEqual(h.farmer.stats.manual, 1)
        self.assertGreaterEqual(job.next_due, h.clock() + 15.5)

    async def test_manual_daily_waits_for_the_reset(self):
        h = Harness()
        await h.run(5)
        await h.farmer.handle(incoming("owo daily", from_me=True, mid=52))
        self.assertGreater(h.farmer.jobs["daily"].next_due, h.clock() + 60)

    async def test_quiet_hours_pause_and_resume(self):
        start = dt.datetime(2026, 5, 28, 22, 58, 0).timestamp()
        h = Harness({"safety": {"quiet_hours": ["23:00-23:30"]}}, start=start)
        await h.run(60)                                # 22:59
        self.assertEqual(h.farmer.status, "running")
        await h.run(120)                               # 23:01
        self.assertIn("quiet", h.farmer.pauses)
        sent = len(h.transport.sent)
        await h.run(600)                               # 23:11
        self.assertIn("quiet", h.farmer.pauses)
        self.assertEqual(len(h.transport.sent), sent)
        await h.run(1500)                              # 23:36
        self.assertNotIn("quiet", h.farmer.pauses)
        self.assertGreater(len(h.transport.sent), sent)

    async def test_command_limit(self):
        h = Harness({"safety": {"max_commands": 10}})
        await h.run(600)
        self.assertTrue(h.farmer.stopped)
        self.assertEqual(h.farmer.stats.sent, 10)
        self.assertEqual(h.farmer.exit_code, of.EXIT_OK)

    async def test_runtime_limit(self):
        h = Harness({"safety": {"max_runtime_minutes": 1.0}})
        await h.run(300)
        self.assertTrue(h.farmer.stopped)
        self.assertLess(h.clock() - h.farmer.started_at, 70)

    async def test_rate_cap_holds_even_with_many_busy_commands(self):
        custom = [{"text": f"cmd{i}", "cooldown": [5, 5.5]} for i in range(10)]
        h = Harness({"farm": {"gap": [2.0, 2.0], "max_messages_per_minute": 6}, "custom": custom})
        await h.run(900)
        times = [t for t, _ in h.transport.sent]
        self.assertGreater(len(times), 30)
        for i, t in enumerate(times):
            self.assertLessEqual(sum(1 for u in times[i:i + 20] if u - t <= 60), 6)

    async def test_send_failures(self):
        for kind, code in (("forbidden", of.EXIT_CHANNEL), ("captcha_required", of.EXIT_SAFETY)):
            h = Harness()
            h.transport.fail_next = of.SendError(kind)
            await h.run(60)
            self.assertTrue(h.farmer.stopped, kind)
            self.assertEqual(h.farmer.exit_code, code, kind)
        h = Harness()
        h.transport.fail_next = of.SendError("ratelimit", retry_after=20)
        self.assertTrue(await h.run_until(lambda: "ratelimit" in h.farmer.pauses, 60))
        await h.run(60)
        self.assertNotIn("ratelimit", h.farmer.pauses)
        h = Harness()
        h.transport.fail_next = of.SendError("network")
        await h.run(60)
        self.assertFalse(h.farmer.stopped)
        self.assertEqual(h.farmer.stats.errors, 1)
        self.assertGreater(h.farmer.stats.sent, 0)

    async def test_hotkey_helpers(self):
        h = Harness()
        await h.run(30)
        h.farmer.toggle_manual_pause()
        self.assertEqual(h.farmer.status, "paused")
        h.farmer.toggle_manual_pause()
        self.assertEqual(h.farmer.status, "running")
        await h.farmer.handle(incoming(CAPTCHA_DM, dm=True))
        h.farmer.captcha_solved()
        self.assertEqual(h.farmer.status, "running")

    async def test_gem_cycle_end_to_end(self):
        quiet = {"owo": {"enabled": False}, "battle": {"enabled": False}, "pray": {"enabled": False},
                 "daily": {"enabled": False}, "cash": {"enabled": False}}
        h = Harness({"commands": quiet, "gems": {"enabled": True, "check_cooldown": 30}}, reply=False)
        self.assertTrue(await h.run_until(lambda: "owo h" in h.texts(), 60))
        await h.farmer.handle(incoming(HUNT_GEMS, mid=60))
        self.assertTrue(await h.run_until(lambda: "owo inv" in h.texts(), 60))
        await h.farmer.handle(incoming(INVENTORY, mid=61))
        self.assertTrue(await h.run_until(lambda: any(t.startswith("owo use") for t in h.texts()), 60))
        self.assertIn("owo use 72", h.texts())
        await h.farmer.handle(incoming(GEM_USED, mid=62))
        self.assertEqual(h.farmer.gems.state, "idle")
        await h.farmer.handle(incoming(HUNT_ALL_GEMS, mid=63))
        self.assertEqual(h.farmer.gems.state, "idle")

    async def test_gems_stay_active_against_the_simulator(self):
        h = Harness({"gems": {"enabled": True, "check_cooldown": 60}})
        await h.run(300)
        self.assertEqual(set(h.sim.active_gems), {"hunting", "empowering", "lucky"})
        used = [t for t in h.texts() if t.startswith("owo use")]
        self.assertEqual(used, ["owo use 51 65 72"])               # lowest tier of each missing type, one command
        self.assertEqual(h.texts().count("owo inv"), 1)
        await h.run(3600)                                          # gems run out and get topped up from what is left
        self.assertGreaterEqual(len([t for t in h.texts() if t.startswith("owo use")]), 2)
        self.assertEqual((h.farmer.stats.slowdowns, h.farmer.stats.missed), (0, 0))

    async def test_pray_alternates_with_curse(self):
        h = Harness({"commands": {"pray": {"targets": [111], "curse_targets": [222]}}})
        self.assertEqual([h.farmer._build_pray() for _ in range(4)],
                         ["curse <@222>", "pray <@111>", "curse <@222>", "pray <@111>"])

    async def test_custom_and_optional_commands_are_scheduled(self):
        h = Harness({"commands": {"lootbox": {"enabled": True, "args": "all"}, "sell": {"enabled": True, "args": "common"}},
                     "custom": [{"text": "zoo", "cooldown": [60, 70]}, {"text": "raw words", "prefix": False,
                                                                        "cooldown": [60, 70]}]})
        await h.run(1500)
        texts = set(h.texts())
        self.assertTrue({"owo zoo", "raw words", "owo lb all", "owo sell common"} <= texts)


class TestGamblingFlow(unittest.IsolatedAsyncioTestCase):
    GAMBLE = {"enabled": True, "stop_loss": 0, "take_profit": 0, "daily_loss_limit": 0, "keep_balance": 0,
              "max_balance_fraction": 0.5, "base_bet": 100}

    async def settle(self, h: Harness) -> None:
        self.assertTrue(await h.run_until(lambda: h.farmer.gambler.pending is None, 60))

    async def check_accounting(self, game: str) -> Harness:
        h = Harness({"gamble": dict(self.GAMBLE, game=game)}, cash=5000)
        await h.run(1800)
        await self.settle(h)
        g = h.farmer.gambler
        self.assertGreater(g.bets, 20)
        net_from_sim = (h.sim.cash - 5000) + 5 * h.sim.hunts - h.sim.daily_total
        self.assertEqual(g.net, net_from_sim)
        self.assertEqual(g.wins + g.losses + g.pushes, g.bets)
        times = [t for t, text in h.transport.sent if text.startswith(("owo cf", "owo slots"))]
        self.assertGreaterEqual(min(b - a for a, b in zip(times, times[1:])), 15.5)
        first_cash = min(t for t, text in h.transport.sent if text == "owo cash")
        self.assertGreater(times[0], first_cash)
        return h

    async def test_coinflip_accounting_matches_the_bank(self):
        await self.check_accounting("coinflip")

    async def test_slots_accounting_matches_the_bank(self):
        await self.check_accounting("slots")

    async def test_stop_loss_ends_betting(self):
        h = Harness({"gamble": dict(self.GAMBLE, game="slots", base_bet=500, stop_loss=600)}, cash=50_000)
        await h.run(3600)
        g = h.farmer.gambler
        self.assertIn("stop-loss", g.halt_reason)
        bets = g.bets
        await h.run(300)
        self.assertEqual(g.bets, bets)
        self.assertGreaterEqual(g.net, -600 - 500)

    async def test_out_of_cowoncy_halts_and_asks_for_a_new_balance(self):
        h = Harness({"gamble": dict(self.GAMBLE, base_bet=100, max_balance_fraction=1.0),
                      "commands": {"daily": {"enabled": False}}}, cash=5000)
        self.assertTrue(await h.run_until(lambda: h.farmer.stats.cash is not None, 120))
        h.sim.cash = 10                                # we still think we have ~5,000
        self.assertTrue(await h.run_until(lambda: bool(h.farmer.gambler.halt_reason), 300))
        self.assertIn("out of cowoncy", h.farmer.gambler.halt_reason)
        self.assertIsNone(h.farmer.gambler.pending)

    async def test_manual_switch_off(self):
        h = Harness({"gamble": dict(self.GAMBLE)}, cash=5000)
        await h.run(200)
        h.farmer.gambler.manual_off = True
        await self.settle(h)
        bets = h.farmer.gambler.bets
        await h.run(120)
        self.assertEqual(h.farmer.gambler.bets, bets)


class TestStateAndAdapters(unittest.IsolatedAsyncioTestCase):
    def test_state_roundtrip_is_atomic_and_throttled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = of.StateFile(path)
            state.set(daily_next=123.5)
            state.save(now=1000.0)
            self.assertTrue(path.exists())
            state.set(daily_next=999.0)
            state.save(now=1005.0)                      # inside the 30 s window: not written yet
            self.assertEqual(of.StateFile(path).data["daily_next"], 123.5)
            state.save(now=1005.0, force=True)
            self.assertEqual(of.StateFile(path).data["daily_next"], 999.0)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["state.json"])

    def test_broken_state_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(of.StateFile(path).data["daily_next"], 0.0)

    async def test_saved_daily_time_is_used_on_start(self):
        h = Harness()
        saved = h.clock() + 7200
        h2 = Harness()
        h2.state.set(daily_next=saved)
        h2.farmer.start()
        self.assertEqual(h2.farmer.jobs["daily"].next_due, saved)
        await h.run(1)

    async def test_lifetime_totals_are_stored_on_stop(self):
        h = Harness()
        await h.run(120)
        h.farmer.stop("test")
        self.assertEqual(h.state.data["lifetime"]["sent"], h.farmer.stats.sent)

    def test_to_incoming_reads_embeds_buttons_and_mentions(self):
        me = SimpleNamespace(id=42)
        owo = SimpleNamespace(id=of.OWO_ID)
        embed = SimpleNamespace(title="Title", description="Body", fields=[SimpleNamespace(name="a", value="b")],
                                footer=SimpleNamespace(text="foot"), author=None)
        button = SimpleNamespace(label="Verify")
        msg = SimpleNamespace(
            id=9, content="hi", embeds=[embed], components=[SimpleNamespace(children=[button])],
            channel=SimpleNamespace(id=77), author=owo, guild=None, mentions=[me],
            reference=SimpleNamespace(resolved=SimpleNamespace(author=me)), attachments=[], jump_url="https://x")
        m = of.to_incoming(msg, 42, edited=True)
        self.assertTrue(all(part in m.text for part in ("hi", "Title", "Body", "a b", "foot")))
        self.assertEqual((m.channel_id, m.author_id, m.is_dm, m.edited), (77, of.OWO_ID, True, True))
        self.assertTrue(m.verify_button and m.mentions_me and m.refs_me and not m.from_me)
        self.assertEqual(m.url, "https://x")

    def test_redaction_hides_tokens_and_webhooks(self):
        token = "AAAAAAAAAAAAAAAAAAAAAAAAAA.BBBBBBB.CCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
        hook = "https://discord.com/api/webhooks/123/secretpart"
        flt = of.Redact([hook])
        record = logging.LogRecord("x", logging.INFO, "", 0, f"token {token} hook {hook}", (), None)
        flt.filter(record)
        self.assertNotIn(token, record.msg)
        self.assertNotIn("secretpart", record.msg)

    async def test_discord_transport_maps_errors(self):
        problem = of.discord_library_problem()
        if problem:
            self.skipTest(problem.splitlines()[0])
        import discord

        class Channel:
            def __init__(self, exc: Exception):
                self.exc = exc

            async def send(self, text: str, **kw: Any) -> None:
                raise self.exc

        response = SimpleNamespace(status=403, reason="Forbidden")
        with self.assertRaises(of.SendError) as ctx:
            await of.DiscordTransport(Channel(discord.Forbidden(response, "no")), True).send("x")
        self.assertEqual(ctx.exception.kind, "forbidden")
        response = SimpleNamespace(status=429, reason="Too Many Requests")
        with self.assertRaises(of.SendError) as ctx:
            await of.DiscordTransport(Channel(discord.HTTPException(response, "slow")), True).send("x")
        self.assertEqual(ctx.exception.kind, "ratelimit")
        with self.assertRaises(of.SendError) as ctx:
            await of.DiscordTransport(Channel(OSError("down")), True).send("x")
        self.assertEqual(ctx.exception.kind, "network")

    async def test_dashboard_renders(self):
        try:
            from rich.console import Console
        except ModuleNotFoundError:
            self.skipTest("rich is not installed")
        h = Harness({"gamble": {"enabled": True}, "gems": {"enabled": True}})
        await h.run(120)
        buf = io.StringIO()
        Console(file=buf, width=110, force_terminal=False).print(of.render_dashboard(h.farmer))
        out = buf.getvalue()
        for word in ("OwO Farmer", "RUNNING", "Schedule", "Gambling", "hunt", "gems"):
            self.assertIn(word, out)
        h.farmer.pause("manual")
        buf = io.StringIO()
        Console(file=buf, width=110, force_terminal=False).print(of.render_dashboard(h.farmer))
        self.assertIn("PAUSED", buf.getvalue())


class TestCommandLine(unittest.TestCase):
    def run_main(self, *argv: str) -> (int, str):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = of.main(list(argv))
        return code, buf.getvalue()

    def test_init_check_and_simulate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "config.toml")
            code, out = self.run_main("--init", "-c", path)
            self.assertEqual(code, 0)
            code, out = self.run_main("--init", "-c", path)
            self.assertEqual(code, of.EXIT_CONFIG)
            code, out = self.run_main("--check", "-c", path)
            self.assertEqual(code, of.EXIT_CONFIG)
            self.assertIn("channel_id", out)
            Path(path).write_text(of.CONFIG_TEMPLATE.replace("channel_id = 0", "channel_id = 5"), encoding="utf-8")
            code, out = self.run_main("--check", "-c", path)
            self.assertEqual(code, 0)
            self.assertIn("hunt", out)
            code, out = self.run_main("--simulate", "20", "-c", path)
            self.assertEqual(code, 0)
            self.assertIn("hunts", out)
            code, out = self.run_main("--simulate", "40", "--sim-captcha", "10", "-c", path)
            self.assertIn("captchas 1", out)

    def test_library_check_is_consistent(self):
        problem = of.discord_library_problem()
        try:
            import discord
            has_fork = hasattr(discord, "CaptchaRequired")
        except ModuleNotFoundError:
            has_fork = False
        self.assertEqual(problem is None, has_fork)
        if problem:
            self.assertIn("pip", problem)

    def test_live_run_without_token_explains_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(of.CONFIG_TEMPLATE.replace("channel_id = 0", "channel_id = 5")
                            .replace('token_env = "DISCORD_TOKEN"', 'token_env = "OWO_NO_SUCH_VAR"'), encoding="utf-8")
            code, out = self.run_main("-c", str(path))
            self.assertEqual(code, of.EXIT_CONFIG)
            self.assertIn("No token", out)


def payload(**over: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {"id": "10", "channel_id": "1", "guild_id": "5", "author": {"id": str(of.OWO_ID)},
                         "content": "", "mentions": [], "embeds": [], "components": [], "attachments": []}
    d.update(over)
    return d


class TestRawPayloads(unittest.IsolatedAsyncioTestCase):
    def test_plain_content_message(self):
        m = of.incoming_from_payload(payload(content=HUNT), me_id=42)
        self.assertEqual((m.id, m.channel_id, m.author_id, m.is_dm, m.edited), (10, 1, of.OWO_ID, False, False))
        self.assertEqual(m.text, HUNT)
        self.assertFalse(m.mentions_me or m.refs_me or m.from_me or m.verify_button)
        self.assertEqual(m.url, "https://discord.com/channels/5/1/10")

    def test_components_v2_text_and_verify_button_are_read(self):
        comps = [{"type": 17, "components": [
            {"type": 10, "content": CAPTCHA_CH},
            {"type": 9, "components": [{"type": 10, "content": "second line"}],
             "accessory": {"type": 2, "label": "Verify", "style": 5}},
            {"type": 14},
            {"type": 1, "components": [{"type": 2, "label": "Help"}]},
        ]}]
        m = of.incoming_from_payload(payload(flags=32768, components=comps), me_id=42)
        self.assertIn("are you a real human", m.text)
        self.assertIn("second line", m.text)
        self.assertTrue(m.verify_button)
        self.assertTrue(of.parse_facts(m.text, verify_button=m.verify_button).captcha)

    def test_buttons_without_verify_do_not_count(self):
        comps = [{"type": 1, "components": [{"type": 2, "label": "Next"}]}]
        self.assertFalse(of.incoming_from_payload(payload(content="x", components=comps), 42).verify_button)

    def test_embeds_contribute_text(self):
        emb = {"title": "T", "description": "D", "fields": [{"name": "n", "value": "v"}], "footer": {"text": "F"},
               "author": {"name": "A"}}
        text = of.incoming_from_payload(payload(embeds=[emb]), 42).text
        for part in ("T", "D", "n v", "F", "A"):
            self.assertIn(part, text)

    def test_dm_mentions_references_and_own_messages(self):
        d = payload(guild_id=None, mentions=[{"id": "42"}], referenced_message={"author": {"id": "42"}},
                    author={"id": "42"}, content="hi")
        del d["guild_id"]
        m = of.incoming_from_payload(d, me_id=42, edited=True)
        self.assertTrue(m.is_dm and m.mentions_me and m.refs_me and m.from_me and m.edited)
        self.assertTrue(m.url.startswith("https://discord.com/channels/@me/"))

    def test_attachment_warning(self):
        m = of.incoming_from_payload(payload(content="⚠️ please solve", attachments=[{"id": "1"}]), 42)
        self.assertTrue(m.warn_attachment)
        self.assertTrue(of.parse_facts(m.text, warn_attachment=m.warn_attachment).captcha)

    def test_partial_or_broken_payloads_are_skipped(self):
        self.assertIsNone(of.incoming_from_payload({"id": "1", "channel_id": "2"}, 42))
        self.assertIsNone(of.incoming_from_payload(payload(author={"id": "x"}), 42))
        self.assertIsNone(of.incoming_from_payload(payload(id=None), 42))
        self.assertIsNotNone(of.incoming_from_payload(payload(embeds=["junk", None], components=["junk", 5]), 42))

    async def test_a_components_v2_captcha_pauses_the_farm(self):
        h = Harness()
        await h.run(60)
        comps = [{"type": 17, "components": [{"type": 10, "content": CAPTCHA_CH}]}]
        await h.farmer.handle(of.incoming_from_payload(payload(components=comps, flags=32768), 1))
        self.assertEqual(h.farmer.stats.captchas, 1)
        self.assertEqual(h.farmer.status, "paused")


class FakeChannel:
    def __init__(self, can_send: bool = True, nick: str = "Nick"):
        self.id = 1
        self.guild = SimpleNamespace(me=SimpleNamespace(nick=nick))
        self.can_send = can_send
        self.sent: List[str] = []

    def permissions_for(self, member: Any) -> Any:
        return SimpleNamespace(send_messages=self.can_send)

    async def send(self, text: str, **kw: Any) -> None:
        self.sent.append(text)


class TestClientGlue(unittest.IsolatedAsyncioTestCase):
    """The Discord client wiring, exercised with fake objects (no network, no login)."""

    def setUp(self) -> None:
        problem = of.discord_library_problem()
        if problem:
            self.skipTest(problem.splitlines()[0])

    def make(self, channel: Optional[FakeChannel] = None):
        cfg = cfg_from()
        clock = of.SimClock(START)
        late = of.LateTransport()
        farmer = of.Farmer(cfg, late, clock=clock, rng=random.Random(1), alerts=of.NullAlerts())
        ready = asyncio.Event()
        client = of.build_client(farmer, late, cfg, ready, logging.getLogger("test_glue"))
        user = SimpleNamespace(id=42, name="Tester", display_name="Tester", global_name=None)
        type(client).user = property(lambda self: user)
        chan = channel or FakeChannel()
        client.get_channel = lambda cid: chan if cid == 1 else None
        return client, farmer, late, ready, chan

    def frame(self, text: str, kind: str = "MESSAGE_CREATE", **over: Any) -> str:
        import json
        return json.dumps({"t": kind, "s": 3, "op": 0, "d": payload(content=text, **over)})

    async def test_ready_starts_the_farmer_and_learns_our_names(self):
        client, farmer, late, ready, chan = self.make()
        await client.on_ready()
        self.assertTrue(ready.is_set())
        self.assertEqual(farmer.me_id, 42)
        self.assertIn("nick", farmer.names)
        self.assertIsNotNone(late.inner)
        await late.send("owo h")
        self.assertEqual(chan.sent, ["owo h"])
        self.assertGreater(farmer.started_at, 0)

    async def test_missing_send_permission_stops_with_a_clear_code(self):
        client, farmer, late, ready, chan = self.make(FakeChannel(can_send=False))
        await client.on_ready()
        self.assertTrue(farmer.stopped)
        self.assertEqual(farmer.exit_code, of.EXIT_CHANNEL)
        self.assertTrue(ready.is_set())

    async def test_unknown_channel_stops(self):
        import discord
        client, farmer, late, ready, chan = self.make()
        client.get_channel = lambda cid: None

        async def fetch(cid: int) -> Any:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "unknown channel")

        client.fetch_channel = fetch
        await client.on_ready()
        self.assertTrue(farmer.stopped)
        self.assertEqual(farmer.exit_code, of.EXIT_CHANNEL)

    async def test_raw_gateway_frames_reach_the_farmer(self):
        client, farmer, late, ready, chan = self.make()
        await client.on_ready()
        await client.on_socket_raw_receive(self.frame(HUNT.replace("Tester", "Nick")))
        self.assertTrue(client.raw_ok)
        self.assertEqual(farmer.stats.hunts, 1)
        await client.on_socket_raw_receive(self.frame(HUNT.replace("Tester", "Nick"), id="11").encode())
        self.assertEqual(farmer.stats.hunts, 2)
        await client.on_socket_raw_receive(self.frame(CF_WIN, "MESSAGE_UPDATE"))
        await client.on_socket_raw_receive(self.frame(BAN.replace("macros", "x"), author={"id": "777"}))
        self.assertFalse(farmer.stopped)

    async def test_noise_and_garbage_frames_are_ignored(self):
        client, farmer, late, ready, chan = self.make()
        await client.on_ready()
        with self.assertLogs("test_glue", level="ERROR"):      # the one broken JSON frame is logged, not raised
            for junk in ('{"t":"PRESENCE_UPDATE","d":{}}', "not json at all",
                         f'{{"t":"MESSAGE_CREATE","d": {of.OWO_ID} broken',
                         self.frame("hello", author={"id": "777"}), b"\xff\xfe", 12345):
                await client.on_socket_raw_receive(junk)
        self.assertFalse(farmer.stopped)
        self.assertEqual(farmer.stats.replies, 0)

    async def test_frames_before_ready_are_ignored(self):
        client, farmer, late, ready, chan = self.make()
        await client.on_socket_raw_receive(self.frame(HUNT))
        self.assertFalse(client.raw_ok)
        self.assertEqual(farmer.stats.hunts, 0)

    async def test_library_events_are_the_fallback_until_raw_frames_arrive(self):
        client, farmer, late, ready, chan = self.make()
        await client.on_ready()
        me = SimpleNamespace(id=42)
        msg = SimpleNamespace(id=20, content=HUNT.replace("Tester", "Nick"), embeds=[], components=[],
                              channel=SimpleNamespace(id=1), author=SimpleNamespace(id=of.OWO_ID), guild=object(),
                              mentions=[], reference=None, attachments=[], jump_url="")
        await client.on_message(msg)                      # no raw frame seen yet: fallback handles it
        self.assertEqual(farmer.stats.hunts, 1)
        await client.on_socket_raw_receive(self.frame(HUNT.replace("Tester", "Nick"), id="21"))
        self.assertEqual(farmer.stats.hunts, 2)
        await client.on_message(msg)                      # raw frames work now: library event is a duplicate
        await client.on_message_edit(msg, msg)
        self.assertEqual(farmer.stats.hunts, 2)
        del me


class TestAlertsWebhook(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_receives_the_alert(self):
        try:
            from aiohttp import web
        except ModuleNotFoundError:
            self.skipTest("aiohttp is not installed")
        received: List[Dict[str, Any]] = []

        async def hook(request: Any) -> Any:
            received.append(await request.json())
            return web.Response(status=204)

        app = web.Application()
        app.router.add_post("/hook", hook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        logs: List[Any] = []
        cfg = SimpleNamespace(bell=False, sound=False, webhook_url=f"http://127.0.0.1:{port}/hook", ping_user_id=99)
        try:
            of.Alerts(cfg, lambda lvl, txt: logs.append((lvl, txt))).notify("captcha", "OwO captcha!", "Solve it", urgent=True)
            for _ in range(100):
                if received:
                    break
                await asyncio.sleep(0.05)
        finally:
            await runner.cleanup()
        self.assertEqual(len(received), 1, logs)
        self.assertTrue(received[0]["content"].startswith("<@99> **OwO captcha!**"))
        self.assertEqual(received[0]["allowed_mentions"], {"users": ["99"]})

    async def test_a_dead_webhook_never_raises(self):
        logs: List[Any] = []
        cfg = SimpleNamespace(bell=False, sound=False, webhook_url="http://127.0.0.1:9/none", ping_user_id=0)
        of.Alerts(cfg, lambda lvl, txt: logs.append((lvl, txt))).notify("x", "t", "b")
        for _ in range(200):                           # Windows can take a couple of seconds to refuse a connection
            if logs:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(any("webhook failed" in txt for _, txt in logs), logs)


USER_ID = 123456789012345678
DM_ID = 555000111222333444
CAPTCHA_REAL = ("**⚠️ |** <@123456789012345678>, are you a real human? Please use the link below so I can check!\n"
                "<:blank:427371936482328596> **|** Please complete this within 10 minutes or it may result in a ban!")
HUNT_BARS = ("**\U0001F331 | Tester**, hunt is empowered by <:cgem1:492572122120585240>`[1/25]` "
             "<:cgem3:510366792024195072>`[46/50]` <:cgem4:510366763255463936>`[0/50]`  !\n"
             "**<:blank:427371936482328596> |** You found:  :bee: :beetle: :butterfly: :rabbit2:\n"
             "<:blank:427371936482328596> **|** <a:2024may_esd:1242757627125956662> "
             "<a:2024may_flower:1242757629608988722> <a:dboar:438847718459179018> gained **13xp**!")
BASE = {"farm": {"channel_id": 1}}


class TestNewConfigOptions(unittest.TestCase):
    def test_hunt_and_battle_timing(self):
        cfg = of.build_config(BASE)[0]
        for name in ("hunt", "battle"):
            c = getattr(cfg.commands, name)
            self.assertEqual((c.cooldown, c.instant_chance, c.delay), ((15.5, 16.2), 0.35, (2.0, 3.0)))
        for bad in ({"instant_chance": 1.5}, {"instant_chance": -0.1}, {"delay": [3, 2]}):
            with self.assertRaises(of.ConfigError):
                of.build_config({**BASE, "commands": {"hunt": bad}})

    def test_breaks(self):
        cfg = of.build_config(BASE)[0]
        self.assertEqual((cfg.safety.break_every_minutes, cfg.safety.break_minutes), (10.0, 3.0))
        off = of.build_config({**BASE, "safety": {"break_every_minutes": 0, "break_minutes": 0}})[0]
        self.assertEqual((off.safety.break_every_minutes, off.safety.break_minutes), (0.0, 0.0))
        for bad in ({"break_every_minutes": 10.0, "break_minutes": 0.0}, {"break_every_minutes": 0.0, "break_minutes": 3.0},
                    {"break_every_minutes": 0.5, "break_minutes": 1.0}):
            with self.assertRaises(of.ConfigError):
                of.build_config({**BASE, "safety": bad})

    def test_dm_alerts(self):
        self.assertFalse(of.build_config(BASE)[0].alerts.dm.enabled)
        with self.assertRaises(of.ConfigError) as ctx:
            of.build_config({**BASE, "alerts": {"dm": {"enabled": True}}})
        self.assertIn("user_id", str(ctx.exception))
        cfg, warns = of.build_config({**BASE, "alerts": {"dm": {"enabled": True, "user_id": USER_ID, "interval_seconds": 2}}})
        self.assertEqual(cfg.alerts.dm.interval_seconds, 15.0)
        self.assertTrue(warns)
        with self.assertRaises(of.ConfigError):
            of.build_config({**BASE, "alerts": {"dm": {"max_pings": 0}}})

    def test_dashboard(self):
        d = of.build_config(BASE)[0].dashboard
        self.assertEqual((d.enabled, d.host, d.port, d.password), (True, "127.0.0.1", 8787, ""))
        for bad in ({"host": "0.0.0.0"}, {"host": "0.0.0.0", "password": "short"}, {"port": 70000}, {"host": ""}):
            with self.assertRaises(of.ConfigError):
                of.build_config({**BASE, "dashboard": bad})
        of.build_config({**BASE, "dashboard": {"host": "0.0.0.0", "password": "long enough password"}})
        of.build_config({**BASE, "dashboard": {"host": "0.0.0.0", "enabled": False}})

    def test_alert_token_resolution(self):
        dm = {"enabled": True, "user_id": USER_ID, "token_env": "OWO_TEST_ALERT_TOKEN"}
        cfg = cfg_from({"alerts": {"dm": dict(dm, token="from-config")}})
        os.environ.pop("OWO_TEST_ALERT_TOKEN", None)
        try:
            self.assertEqual(of.resolve_alert_token(cfg, "main"), "from-config")
            os.environ["OWO_TEST_ALERT_TOKEN"] = "from-env"
            self.assertEqual(of.resolve_alert_token(cfg, "main"), "from-env")
            with self.assertRaises(of.ConfigError):
                of.resolve_alert_token(cfg, "from-env")              # must not be the farming account
            os.environ["OWO_TEST_ALERT_TOKEN"] = "has space"
            with self.assertRaises(of.ConfigError):
                of.resolve_alert_token(cfg, "main")
        finally:
            os.environ.pop("OWO_TEST_ALERT_TOKEN", None)
        with self.assertRaises(of.ConfigError):
            of.resolve_alert_token(cfg_from({"alerts": {"dm": dm}}), "main")


class TestRealMessages(unittest.IsolatedAsyncioTestCase):
    """The exact messages from a real session."""

    def test_gem_bars_are_read(self):
        f = of.parse_facts(HUNT_BARS)
        self.assertTrue(f.hunt)
        self.assertEqual(f.gem_levels, {"hunting": (1, 25), "empowering": (46, 50), "lucky": (0, 50)})
        self.assertEqual(f.gems_active, frozenset({"hunting", "empowering"}))      # [0/50] counts as used up
        self.assertEqual((f.xp, f.animals), (13, 4))

    def test_plain_hunt_has_one_animal_and_no_gems(self):
        f = of.parse_facts(HUNT)
        self.assertEqual((f.animals, f.xp, f.gem_levels), (1, 0, {}))

    def test_exhausted_gem_triggers_a_check_right_away(self):
        g = of.GemManager(cfg_from({"gems": {"enabled": True}}).gems)
        self.assertTrue(g.on_hunt(of.parse_facts(HUNT_BARS).gems_active, 1000.0))
        self.assertEqual(g.missing, ["lucky"])

    async def test_the_real_captcha_is_recognised_by_its_mention(self):
        f = of.parse_facts(CAPTCHA_REAL)
        self.assertTrue(f.captcha)
        self.assertFalse(f.ban)
        h = Harness()
        h.farmer.set_identity(USER_ID, ["Tester"])
        await h.run(30)
        m = of.incoming_from_payload({"id": "9", "channel_id": "1", "guild_id": "5", "author": {"id": str(of.OWO_ID)},
                                      "content": CAPTCHA_REAL}, USER_ID)
        self.assertTrue(m.mentions_me)                                  # found in the text itself, not only the list
        await h.farmer.handle(m)
        self.assertEqual(h.farmer.stats.captchas, 1)
        self.assertEqual(h.farmer.status, "paused")
        self.assertIn("captcha", h.farmer.pauses)
        self.assertIsNone(h.farmer.pauses["captcha"].until)             # waits for you, not a timed guess

    async def test_verified_and_the_c_key_tell_the_alerts(self):
        h = Harness()
        await h.run(30)
        h.sim.trigger_captcha()
        await h.run(3)
        await h.farmer.handle(incoming(VERIFIED, dm=True))
        h.farmer.captcha_solved()
        self.assertEqual([k for k, _, _ in h.alerts.sent if k == "verified"], ["verified", "verified"])


class TestHuntTiming(unittest.IsolatedAsyncioTestCase):
    ONLY_HUNT = {"commands": {"owo": {"enabled": False}, "battle": {"enabled": False}, "pray": {"enabled": False},
                              "daily": {"enabled": False}, "cash": {"enabled": False}}}

    async def intervals(self, chance: float, hours: int = 4) -> List[float]:
        over = deep_update(copy.deepcopy(self.ONLY_HUNT), {"commands": {"hunt": {"instant_chance": chance}}})
        h = Harness(over, seed=3)
        await h.run(hours * 3600)
        times = h.times("owo h")
        return [b - a for a, b in zip(times, times[1:])]

    async def test_mix_of_instant_and_delayed(self):
        gaps = await self.intervals(0.35)
        self.assertGreater(len(gaps), 600)
        instant = [g for g in gaps if g <= 16.5]
        delayed = [g for g in gaps if g >= 17.5]
        self.assertEqual(len(instant) + len(delayed), len(gaps))             # nothing in between
        self.assertTrue(all(15.5 <= g for g in instant))
        self.assertTrue(all(g <= 19.5 for g in delayed))
        self.assertTrue(0.25 < len(instant) / len(gaps) < 0.45, len(instant) / len(gaps))

    async def test_always_instant_and_never_instant(self):
        always = await self.intervals(1.0, hours=1)
        never = await self.intervals(0.0, hours=1)
        self.assertTrue(all(15.5 <= g <= 16.5 for g in always))
        self.assertTrue(all(17.5 <= g <= 19.5 for g in never))

    def test_battle_uses_the_same_rule(self):
        for name in ("hunt", "battle"):
            farmer = Harness().farmer
            when = farmer.jobs[name].after_send
            gaps = [when(0.0) for _ in range(2000)]
            instant = sum(1 for g in gaps if g <= 16.2)
            self.assertTrue(all(15.5 <= g <= 16.2 or 17.5 <= g <= 19.2 for g in gaps))
            self.assertTrue(0.30 < instant / len(gaps) < 0.40, (name, instant / len(gaps)))

    def test_typing_a_command_yourself_uses_it_too(self):
        farmer = Harness().farmer
        self.assertIs(farmer.jobs["hunt"].after_manual is not None, True)


class TestBreaks(unittest.IsolatedAsyncioTestCase):
    CYCLE = {"safety": {"break_every_minutes": 10.0, "break_minutes": 3.0}}

    async def test_ten_minutes_of_work_then_three_of_rest(self):
        h = Harness(self.CYCLE)
        spans, begun = [], None
        end = h.clock() + 45 * 60
        while h.clock() < end:
            h.clock.advance(0.25)
            await h.sim.deliver()
            await h.farmer.tick()
            on_break = "break" in h.farmer.pauses
            if on_break and begun is None:
                begun = h.clock()
            elif not on_break and begun is not None:
                spans.append((begun, h.clock()))
                begun = None
        self.assertEqual(h.farmer.stats.breaks, 3)
        self.assertEqual(len(spans), 3)
        for a, b in spans:
            self.assertAlmostEqual(b - a, 180, delta=1.0)
            self.assertFalse([t for t, _ in h.transport.sent if a <= t < b], "sent something during a break")
        starts = [a for a, _ in spans]
        self.assertAlmostEqual(starts[1] - starts[0], 780, delta=2.0)          # 10 min working + 3 min resting
        self.assertAlmostEqual(starts[2] - starts[1], 780, delta=2.0)
        self.assertGreater(h.farmer.stats.sent, 200)
        self.assertEqual((h.farmer.stats.missed, h.farmer.stats.slowdowns), (0, 0))

    async def test_breaks_are_quiet_events(self):
        h = Harness(self.CYCLE)
        await h.run(11 * 60)
        self.assertEqual(h.farmer.stats.breaks, 1)
        self.assertFalse([k for k, _, _ in h.alerts.sent])                       # no bell, no webhook
        note = [e for e in h.farmer.events if "scheduled break" in e.text][0]
        self.assertEqual(note.level, "info")

    async def test_only_time_spent_running_counts(self):
        h = Harness(self.CYCLE)
        await h.run(5 * 60)
        h.farmer.toggle_manual_pause()
        await h.run(20 * 60)                                                      # paused: the break timer waits
        h.farmer.toggle_manual_pause()
        await h.run(290)
        self.assertEqual(h.farmer.stats.breaks, 0)
        await h.run(40)
        self.assertEqual(h.farmer.stats.breaks, 1)

    async def test_off_means_off(self):
        h = Harness({"safety": {"break_every_minutes": 0.0, "break_minutes": 0.0}})
        await h.run(60 * 60)
        self.assertEqual(h.farmer.stats.breaks, 0)
        self.assertIsNone(h.farmer.next_break_in(h.clock()))

    async def test_next_break_countdown(self):
        h = Harness(self.CYCLE)
        await h.run(120)
        left = h.farmer.next_break_in(h.clock())
        self.assertTrue(470 < left < 490, left)


class TestGemBars(unittest.IsolatedAsyncioTestCase):
    QUIET = {"owo": {"enabled": False}, "battle": {"enabled": False}, "pray": {"enabled": False},
             "daily": {"enabled": False}, "cash": {"enabled": False}}

    async def test_an_empty_bar_gets_a_new_gem_straight_away(self):
        h = Harness({"commands": self.QUIET, "gems": {"enabled": True, "check_cooldown": 30}}, reply=False)
        self.assertTrue(await h.run_until(lambda: "owo h" in h.texts(), 60))
        await h.farmer.handle(incoming(HUNT_BARS, mid=60))
        self.assertEqual(h.farmer.gem_levels["lucky"], (0, 50))
        self.assertTrue(await h.run_until(lambda: "owo inv" in h.texts(), 60))
        bag = "**\U0001F4E6 | Tester's Inventory**\n`072`<:cgem4:3>⁰³  `074`<:rgem4:4>⁰¹  `065`<:cgem3:2>⁰¹"
        await h.farmer.handle(incoming(bag, mid=61))
        self.assertTrue(await h.run_until(lambda: any(t.startswith("owo use") for t in h.texts()), 60))
        self.assertIn("owo use 72", h.texts())                    # only the lucky gem: the other two are still running
        self.assertNotIn("owo use 72 65", h.texts())

    async def test_gems_are_swapped_before_the_next_hunt(self):
        h = Harness({"gems": {"enabled": True, "check_cooldown": 30}}, seed=4)
        h.sim.inventory = {"051": 60, "052": 60, "065": 60, "066": 60, "072": 60, "073": 60}
        await h.run(120 * 60)
        used = [t for t in h.texts() if t.startswith("owo use")]
        self.assertGreaterEqual(len(used), 12)                    # a fresh set about every 25 hunts
        for kind in ("hunting", "empowering", "lucky"):
            # Only the very first hunt may run before any gem is on. Without the bar, every refill would miss one too
            # (15 hunts in this run instead of 1).
            self.assertLessEqual(h.sim.hunts_without[kind], 2, (kind, h.sim.hunts_without))
        self.assertEqual((h.farmer.stats.missed, h.farmer.stats.slowdowns), (0, 0))

    async def test_gem_levels_reach_the_stats(self):
        h = Harness({"gems": {"enabled": True}})
        await h.run(30)
        xp, animals = h.farmer.stats.xp, h.farmer.stats.animals
        await h.farmer.handle(incoming(HUNT_BARS, mid=62))
        snap = of.snapshot(h.farmer)
        self.assertEqual(snap["gems"]["levels"]["empowering"], {"left": 46, "total": 50})
        self.assertEqual((h.farmer.stats.xp - xp, h.farmer.stats.animals - animals), (13, 4))


class DmSender:
    def __init__(self, fail_with: Optional[Exception] = None, channel_id: int = DM_ID):
        self.sent: List[str] = []
        self.fail_with, self.channel_id = fail_with, channel_id

    async def __call__(self, text: str) -> int:
        if self.fail_with:
            raise self.fail_with
        self.sent.append(text)
        return self.channel_id


def dm_cfg(**over: Any) -> SimpleNamespace:
    base = dict(enabled=True, token_env="X", token="", user_id=USER_ID, interval_seconds=0.02, max_pings=50)
    base.update(over)
    return SimpleNamespace(**base)


async def settle(cond: Callable[[], bool], timeout: float = 3.0) -> bool:
    import time as _time
    end = _time.monotonic() + timeout
    while _time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return cond()


class LoginFailure(Exception):
    """Same name as discord's, so the pinger treats it as a hard failure."""


class TestDmPinger(unittest.IsolatedAsyncioTestCase):
    TOKEN = "SECRET-ALERT-TOKEN-VALUE"

    def make(self, sender: Any = None, **cfg: Any):
        sender = sender or DmSender()
        logs: List[Any] = []
        pinger = of.DmPinger(dm_cfg(**cfg), self.TOKEN, lambda lvl, txt: logs.append((lvl, txt)), sender=sender)
        return pinger, sender, logs

    async def test_pings_you_until_you_say_stop(self):
        pinger, sender, _ = self.make()
        pinger.alert("captcha", "OwO captcha!", "Solve it in Discord. https://discord.com/channels/1/2/3")
        self.assertTrue(await settle(lambda: len(sender.sent) >= 3))
        first = sender.sent[0]
        self.assertTrue(first.startswith(f"<@{USER_ID}>"))                      # a real mention, so it pings
        self.assertIn("https://discord.com/channels/1/2/3", first)
        self.assertIn("stop", first.lower())
        self.assertEqual(pinger.channel_id, DM_ID)
        self.assertTrue(pinger.wants_stop(incoming("Stop!", dm=True, from_me=True, channel=DM_ID)))
        pinger.stop("test")
        count = len(sender.sent)
        await asyncio.sleep(0.15)
        self.assertEqual(len(sender.sent), count)
        self.assertFalse(pinger.active)
        self.assertFalse([m for m in sender.sent if self.TOKEN in m])

    async def test_only_your_stop_in_that_dm_counts(self):
        pinger, sender, _ = self.make()
        pinger.alert("captcha", "c", "")
        self.assertTrue(await settle(lambda: sender.sent))
        no = [incoming("stop", dm=True, from_me=False, channel=DM_ID),                # not you
              incoming("stop", dm=False, from_me=True, channel=DM_ID),                # a server channel
              incoming("stop", dm=True, from_me=True, channel=DM_ID + 1),             # another chat
              incoming("don't stop", dm=True, from_me=True, channel=DM_ID),
              incoming("stopwatch", dm=True, from_me=True, channel=DM_ID),
              incoming("", dm=True, from_me=True, channel=DM_ID)]
        self.assertFalse([m.text for m in no if pinger.wants_stop(m)])
        for text in ("stop", "STOP", "Stop.", "stop now", "stop!!"):
            self.assertTrue(pinger.wants_stop(incoming(text, dm=True, from_me=True, channel=DM_ID)), text)
        pinger.stop("done")

    async def test_stops_by_itself_after_max_pings(self):
        pinger, sender, _ = self.make(max_pings=3)
        pinger.alert("captcha", "c", "")
        self.assertTrue(await settle(lambda: not pinger.active and len(sender.sent) == 3))
        await asyncio.sleep(0.1)
        self.assertEqual(len(sender.sent), 3)
        self.assertTrue(pinger.silenced)
        pinger.alert("captcha", "again", "")                                            # reminders must not restart it
        await asyncio.sleep(0.1)
        self.assertEqual(len(sender.sent), 3)

    async def test_after_stop_reminders_stay_quiet_until_the_captcha_is_over(self):
        pinger, sender, _ = self.make()
        pinger.alert("captcha", "c", "")
        self.assertTrue(await settle(lambda: sender.sent))
        pinger.stop("you said stop")
        count = len(sender.sent)
        pinger.alert("captcha", "reminder", "")
        await asyncio.sleep(0.1)
        self.assertEqual(len(sender.sent), count)
        pinger.alert("verified", "solved", "")                                          # captcha over: re-armed
        self.assertFalse(pinger.silenced)
        pinger.alert("captcha", "a new one", "")
        self.assertTrue(await settle(lambda: len(sender.sent) > count))
        pinger.stop("end")

    async def test_verified_ends_the_pings(self):
        pinger, sender, _ = self.make()
        pinger.alert("captcha", "c", "")
        self.assertTrue(await settle(lambda: len(sender.sent) >= 2))
        pinger.alert("verified", "ok", "")
        count = len(sender.sent)
        await asyncio.sleep(0.12)
        self.assertEqual(len(sender.sent), count)
        self.assertFalse(pinger.active)

    async def test_repeated_failures_switch_it_off(self):
        pinger, sender, logs = self.make(DmSender(RuntimeError("blocked")))
        pinger.alert("captcha", "c", "")
        self.assertTrue(await settle(lambda: pinger.dead))
        self.assertFalse(pinger.active)
        self.assertTrue(any("switched off" in t for _, t in logs))
        self.assertFalse(any(self.TOKEN in t for _, t in logs))

    async def test_a_rejected_alert_account_is_a_hard_failure(self):
        pinger, sender, logs = self.make(DmSender(LoginFailure("bad token")))
        pinger.alert("captcha", "c", "")
        self.assertTrue(await settle(lambda: pinger.dead))
        self.assertEqual(pinger.failures, 1)
        self.assertTrue(any("rejected" in t for _, t in logs))

    async def test_one_off_alerts_for_bans_and_stops(self):
        pinger, sender, _ = self.make()
        pinger.alert("stop", "OwO Farmer stopped", "OwO says this account has been banned")
        await pinger.drain()
        self.assertEqual(len(sender.sent), 1)
        self.assertIn("banned", sender.sent[0])
        self.assertTrue(sender.sent[0].startswith(f"<@{USER_ID}>"))
        self.assertFalse(pinger.active)                                                  # a single message, not a series
        pinger.alert("slowdown", "x", "y")                                               # other kinds are not DMed
        await pinger.drain()
        self.assertEqual(len(sender.sent), 1)

    async def test_whole_chain_captcha_stop_verified(self):
        h = Harness()
        sender = DmSender()
        pinger = of.DmPinger(dm_cfg(), self.TOKEN, h.farmer.log, sender=sender)
        h.farmer.alerts = of.Alerts(SimpleNamespace(bell=False, sound=False, webhook_url="", ping_user_id=0),
                                    h.farmer.log, pinger)
        await h.run(60)
        h.sim.trigger_captcha()
        await h.run(3)
        self.assertTrue(await settle(lambda: len(sender.sent) >= 2))
        self.assertEqual(h.farmer.status, "paused")
        await h.farmer.handle(incoming("stop", dm=True, from_me=True, channel=DM_ID))   # you reply in the DM
        count = len(sender.sent)
        await asyncio.sleep(0.12)
        self.assertEqual(len(sender.sent), count)
        await h.run(150)                                       # the farmer's own 45 s reminders must not restart it
        await asyncio.sleep(0.1)
        self.assertEqual(len(sender.sent), count)
        h.sim.solve_captcha()
        await h.run(100)
        self.assertEqual(h.farmer.status, "running")
        self.assertFalse(pinger.silenced)

    async def test_a_ban_sends_one_dm_and_exits(self):
        h = Harness()
        sender = DmSender()
        pinger = of.DmPinger(dm_cfg(), self.TOKEN, h.farmer.log, sender=sender)
        h.farmer.alerts = of.Alerts(SimpleNamespace(bell=False, sound=False, webhook_url="", ping_user_id=0),
                                    h.farmer.log, pinger)
        await h.run(30)
        await h.farmer.handle(incoming(BAN, dm=True))
        await h.farmer.alerts.drain()
        self.assertTrue(h.farmer.stopped)
        self.assertEqual(len(sender.sent), 1)

    async def test_discord_send_path_uses_the_library(self):
        problem = of.discord_library_problem()
        if problem:
            self.skipTest(problem.splitlines()[0])
        import discord
        calls: List[Any] = []

        class FakeDM:
            id = DM_ID

            async def send(self, text: str) -> None:
                calls.append(("send", text))

        class FakeClient:
            async def login(self, token: str) -> None:
                calls.append(("login", token))

            async def create_dm(self, user: Any) -> FakeDM:
                calls.append(("create_dm", user.id))
                return FakeDM()

            async def close(self) -> None:
                calls.append(("close",))

        real = discord.Client
        discord.Client = FakeClient                                   # type: ignore[misc,assignment]
        try:
            pinger = of.DmPinger(dm_cfg(), self.TOKEN, lambda *_: None)
            self.assertEqual(await pinger._discord_send("one"), DM_ID)
            await pinger._discord_send("two")
            await pinger.close()
        finally:
            discord.Client = real                                     # type: ignore[misc]
        self.assertEqual([c[0] for c in calls], ["login", "create_dm", "send", "send", "close"])
        self.assertEqual(calls[0][1], self.TOKEN)
        self.assertEqual(calls[1][1], USER_ID)


class TestWebDashboard(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        try:
            import aiohttp  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("aiohttp is not installed")

    async def serve(self, **dash: Any):
        h = Harness({"gems": {"enabled": True}, "gamble": {"enabled": True},
                     "account": {"token": "TOPSECRETTOKEN"},
                     "alerts": {"webhook_url": "https://discord.com/api/webhooks/123/WEBHOOKSECRET",
                                "dm": {"enabled": True, "user_id": USER_ID, "token": "ALTSECRETTOKEN"}}})
        await h.run(300)
        await h.farmer.handle(incoming(HUNT_BARS, mid=77))
        cfg = SimpleNamespace(host="127.0.0.1", port=0, password="")
        for key, value in dash.items():
            setattr(cfg, key, value)
        web = of.WebDashboard(h.farmer, cfg)
        url = await web.start()
        self.addAsyncCleanup(web.stop)
        return h, web, url

    async def get(self, url: str, **kw: Any):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, **kw) as resp:
                return resp.status, dict(resp.headers), await resp.text()

    async def test_page_and_stats(self):
        import json
        h, web, url = await self.serve()
        status, headers, body = await self.get(url + "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("OwO Farmer", body)
        status, headers, body = await self.get(url + "/api/stats")
        data = json.loads(body)
        self.assertEqual((status, data["status"]), (200, "running"))
        self.assertGreater(data["stats"]["hunts"], 5)
        self.assertEqual(data["gems"]["levels"]["lucky"], {"left": 0, "total": 50})
        self.assertTrue(data["jobs"] and data["events"] and data["commands"])
        self.assertFalse([e for e in data["events"] if e["text"].startswith("sent: ")])   # events are the notable ones
        self.assertLessEqual(len(data["commands"]), 12)
        self.assertTrue(all(c["text"].startswith("owo") for c in data["commands"]))
        self.assertIsNotNone(data["gamble"])
        self.assertEqual(data["break"]["enabled"], False)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])

    async def test_no_secret_ever_appears(self):
        import json
        h, web, url = await self.serve(password="hunter2-hunter2")
        auth = basic("me", "hunter2-hunter2")
        pages = [await self.get(url + path, headers=auth) for path in ("/", "/api/stats")]
        everything = json.dumps(of.snapshot(h.farmer)) + "".join(body for _, _, body in pages)
        for secret in ("TOPSECRETTOKEN", "ALTSECRETTOKEN", "WEBHOOKSECRET", "hunter2-hunter2"):
            self.assertNotIn(secret, everything)

    async def test_read_only_and_unknown_paths(self):
        import aiohttp
        h, web, url = await self.serve()
        status, headers, _ = await self.get(url + "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        async with aiohttp.ClientSession() as session:
            for verb in ("post", "put", "delete"):
                async with getattr(session, verb)(url + "/api/stats") as resp:
                    self.assertEqual(resp.status, 405, verb)

    async def test_a_foreign_host_name_is_refused(self):
        h, web, url = await self.serve()
        status, _, _ = await self.get(url + "/api/stats", headers={"Host": "evil.example:%d" % web.port})
        self.assertEqual(status, 403)
        status, _, _ = await self.get(url + "/api/stats", headers={"Host": "localhost:%d" % web.port})
        self.assertEqual(status, 200)

    async def test_password(self):
        h, web, url = await self.serve(password="correct horse battery")
        status, headers, _ = await self.get(url + "/api/stats")
        self.assertEqual(status, 401)
        self.assertIn("Basic", headers["WWW-Authenticate"])
        for wrong in ("wrong", "correct horse batter", "correct horse battery!", ""):
            status, _, _ = await self.get(url + "/api/stats", headers=basic("x", wrong))
            self.assertEqual(status, 401, wrong)
        status, _, _ = await self.get(url + "/api/stats", headers=basic("anyone", "correct horse battery"))
        self.assertEqual(status, 200)
        status, _, _ = await self.get(url + "/api/stats", headers={"Authorization": "Basic !!!not-base64!!!"})
        self.assertEqual(status, 401)

    async def test_reachable_from_other_devices_only_with_a_password(self):
        h, web, url = await self.serve(host="0.0.0.0", password="correct horse battery")
        self.assertTrue(url.startswith("http://localhost:"))
        status, _, _ = await self.get(url + "/api/stats")
        self.assertEqual(status, 401)
        status, _, _ = await self.get(url + "/api/stats", headers={**basic("x", "correct horse battery"),
                                                                    "Host": "192.168.1.20:%d" % web.port})
        self.assertEqual(status, 200)

    async def test_stop_and_port_in_use(self):
        h, web, url = await self.serve()
        second = of.WebDashboard(h.farmer, SimpleNamespace(host="127.0.0.1", port=web.port, password=""))
        with self.assertRaises(OSError):
            await second.start()
        await web.stop()
        with self.assertRaises(OSError):                      # connection refused: the page is really gone
            await self.get(url + "/api/stats")

    async def test_snapshot_shapes(self):
        import json
        h = Harness({"safety": {"break_every_minutes": 10.0, "break_minutes": 3.0}})
        await h.run(11 * 60)
        snap = of.snapshot(h.farmer)
        json.dumps(snap)
        self.assertTrue(snap["break"]["on_break"])
        self.assertEqual(snap["stats"]["breaks"], 1)
        self.assertIsNone(snap["gems"])
        self.assertIsNone(snap["gamble"])
        self.assertEqual(snap["dm_alerts"], {"enabled": False, "pinging": False, "pings": 0, "silenced": False, "dead": False})
        reason = snap["pauses"][0]
        self.assertEqual(reason["reason"], "break")
        self.assertTrue(0 < reason["ends_in"] <= 180)


class TestCommandLineNewOptions(unittest.TestCase):
    def run_main(self, *argv: str):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = of.main(list(argv))
        return code, buf.getvalue()

    def test_check_describes_the_new_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            text = of.CONFIG_TEMPLATE.replace("channel_id = 0", "channel_id = 5")
            text = text.replace("[alerts.dm]                        # on a captcha", "[alerts.dm]  # on a captcha")
            path.write_text(text, encoding="utf-8")
            code, out = self.run_main("--check", "-c", str(path))
            self.assertEqual(code, 0)
            for expected in ("hunt timing: 35% of the time", "battle timing", "breaks: rest 3 min after every 10 min",
                             "DM alerts: off", "web dashboard: http://127.0.0.1:8787"):
                self.assertIn(expected, out)

    def test_dm_alerts_need_their_token_before_anything_connects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            text = of.CONFIG_TEMPLATE.replace("channel_id = 0", "channel_id = 5")
            text = text.replace('token_env = "DISCORD_TOKEN"', 'token_env = "OWO_TEST_MAIN_TOKEN"')
            text = text.replace('token_env = "ALERT_ACCOUNT_TOKEN"', 'token_env = "OWO_TEST_NO_ALERT_TOKEN"')
            text = text.replace("enabled = false\ntoken_env", "enabled = true\ntoken_env")
            text = text.replace("user_id = 0                        # REQUIRED when enabled: the user ID that receives the DMs (yours)",
                                f"user_id = {USER_ID}")
            path.write_text(text, encoding="utf-8")
            os.environ["OWO_TEST_MAIN_TOKEN"] = "main-account-token"
            os.environ.pop("OWO_TEST_NO_ALERT_TOKEN", None)
            try:
                code, out = self.run_main("-c", str(path))
            finally:
                os.environ.pop("OWO_TEST_MAIN_TOKEN", None)
            self.assertEqual(code, of.EXIT_CONFIG)
            self.assertIn("OWO_TEST_NO_ALERT_TOKEN", out)


if __name__ == "__main__":
    unittest.main()
