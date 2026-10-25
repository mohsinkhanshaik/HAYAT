"""An intruder is detected and locked out; the owner never is; nothing is attacked."""
from __future__ import annotations

import ipaddress

from app.security.intrusion import BAN_LADDER, BAN_THRESHOLD, FLOOD_LIMIT, IntrusionShield


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def shield(tmp_path=None, clock=None, trusted=()):
    clock = clock or Clock()
    nets = tuple(ipaddress.ip_network(n) for n in trusted)
    return IntrusionShield(tmp_path / "bans.json" if tmp_path else None, clock=clock, wall_clock=clock,
                           trusted_networks=nets), clock


ATTACKER = "203.0.113.9"


# ------------------------------------------------------------------ the owner is safe
def test_the_owner_is_never_scored_or_banned():
    sh, _ = shield(trusted=["192.168.1.0/24"])
    for _ in range(20):
        assert not sh.note_failed_login("127.0.0.1").blocked
        assert not sh.inspect("192.168.1.50", "GET", "/../../etc/passwd").blocked
    assert sh.status()["banned_count"] == 0
    assert sh.trust("127.0.0.1") and sh.trust("192.168.1.7") and not sh.trust(ATTACKER)


# ------------------------------------------------------------------ detection & lockout
def test_repeated_wrong_passphrases_get_locked_out():
    sh, _ = shield()
    decisions = [sh.note_failed_login(ATTACKER) for _ in range(BAN_THRESHOLD)]
    assert decisions[-1].blocked and decisions[-1].status == 403
    assert sh.banned_for(ATTACKER) > 0
    # Once banned, an ordinary request is tarpitted, not served.
    verdict = sh.inspect(ATTACKER, "POST", "/api/login")
    assert verdict.action == "tarpit" and verdict.delay > 0 and verdict.status == 503


def test_a_single_path_traversal_probe_is_enough():
    sh, _ = shield()
    verdict = sh.inspect(ATTACKER, "GET", "/../../windows/win.ini")
    assert verdict.blocked and sh.banned_for(ATTACKER) > 0


def test_scanner_probes_for_secret_files_are_caught():
    for path in ("/.env", "/.git/config"):
        sh, _ = shield()
        assert sh.inspect(ATTACKER, "GET", path).blocked
        assert sh.banned_for(ATTACKER) > 0
    sh, _ = shield()
    assert not sh.inspect(ATTACKER, "GET", "/wp-login.php").blocked
    assert sh.inspect(ATTACKER, "GET", "/phpmyadmin/").blocked
    assert sh.status()["banned_count"] == 1


def test_injection_markers_in_the_query_are_caught():
    sh, _ = shield()
    sh.inspect(ATTACKER, "GET", "/api/status", query="q=1 UNION SELECT password FROM users")
    sh.inspect(ATTACKER, "GET", "/", query="x=<script>alert(1)</script>")
    assert sh.banned_for(ATTACKER) > 0


def test_a_flood_of_requests_is_throttled_into_a_ban():
    sh, clock = shield()
    banned = False
    for _ in range(FLOOD_LIMIT + 5):
        if sh.inspect(ATTACKER, "GET", "/api/status").blocked:
            banned = True
    assert banned and sh.banned_for(ATTACKER) > 0


def test_a_normal_client_is_left_alone():
    sh, clock = shield()
    for _ in range(5):
        assert sh.inspect("198.51.100.4", "GET", "/").action == "allow"
        assert sh.inspect("198.51.100.4", "POST", "/api/chat").action == "allow"
        clock.advance(1)
    assert sh.status()["banned_count"] == 0


# ------------------------------------------------------------------ nothing offensive
def test_the_shield_only_delays_and_denies_never_attacks():
    sh, _ = shield()
    for _ in range(BAN_THRESHOLD):
        sh.note_failed_login(ATTACKER)
    verdict = sh.inspect(ATTACKER, "GET", "/")
    # Its whole arsenal: block, tarpit, allow. No action reaches back to a source.
    assert verdict.action in {"block", "tarpit"}
    assert not hasattr(verdict, "payload") and not hasattr(verdict, "counterattack")


# ------------------------------------------------------------------ escalation, recovery, persistence
def test_bans_get_longer_each_time():
    sh, clock = shield()
    lengths = []
    for _ in range(3):
        for _ in range(BAN_THRESHOLD):
            sh.note_failed_login(ATTACKER)
        lengths.append(round(sh.banned_for(ATTACKER)))
        clock.advance(lengths[-1] + 1)          # wait out the ban, then reoffend
    assert lengths[0] == BAN_LADDER[0] and lengths[1] == BAN_LADDER[1] and lengths[2] == BAN_LADDER[2]


def test_a_correct_login_clears_the_offence_score():
    sh, _ = shield()
    for _ in range(BAN_THRESHOLD - 1):
        sh.note_failed_login(ATTACKER)
    sh.note_success(ATTACKER)
    assert not sh.note_failed_login(ATTACKER).blocked      # score was reset, so one more is not a ban


def test_the_owner_can_unban():
    sh, _ = shield()
    for _ in range(BAN_THRESHOLD):
        sh.note_failed_login(ATTACKER)
    assert sh.banned_for(ATTACKER) > 0
    assert sh.unban(ATTACKER) and sh.banned_for(ATTACKER) == 0
    assert not sh.unban("someone.else")


def test_a_ban_survives_a_restart(tmp_path):
    sh, clock = shield(tmp_path)
    for _ in range(BAN_THRESHOLD):
        sh.note_failed_login(ATTACKER)
    assert sh.banned_for(ATTACKER) > 0
    reborn = IntrusionShield(tmp_path / "bans.json", clock=clock, wall_clock=clock)
    assert reborn.banned_for(ATTACKER) > 0
    assert reborn.status()["banned"][0]["source"] == ATTACKER


def test_an_expired_ban_is_not_reloaded(tmp_path):
    sh, clock = shield(tmp_path)
    for _ in range(BAN_THRESHOLD):
        sh.note_failed_login(ATTACKER)
    clock.advance(BAN_LADDER[0] + 5)
    reborn = IntrusionShield(tmp_path / "bans.json", clock=clock, wall_clock=clock)
    assert reborn.banned_for(ATTACKER) == 0


# ------------------------------------------------------------------ events
def test_a_ban_is_announced_as_an_event():
    published = []
    clock = Clock()
    sh = IntrusionShield(None, events=type("E", (), {"publish": lambda self, *a, **k: published.append((a, k))})(),
                         clock=clock, wall_clock=clock)
    for _ in range(BAN_THRESHOLD):
        sh.note_failed_login(ATTACKER)
    assert any(args[0] == "INTRUSION_BANNED" for args, _ in published)


def test_secret_theft_is_locked_out_and_gets_only_synthetic_data():
    sh, _ = shield()
    assert sh.inspect(ATTACKER, 'GET', '/.env').blocked
    content_type, body = sh.decoy_response(ATTACKER, 'GET', '/.env')
    assert content_type == 'text/plain' and 'API_KEY=' in body and 'example.invalid' in body
    assert sh.decoy_response(ATTACKER, 'GET', '/.env')[1] == body
    assert sh.decoy_response(ATTACKER, 'POST', '/api/chat') is None
    assert sh.decoy_response('127.0.0.1', 'GET', '/.env') is None
    assert sh.decoy_response('198.51.100.4', 'GET', '/.env') is None
