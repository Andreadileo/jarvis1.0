from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.policy import (
    PolicyConfig,
    PolicyInput,
    Rule,
    decide,
    in_quiet_hours,
    match_rule,
    retry_at,
)

ROME = ZoneInfo("Europe/Rome")
CFG = PolicyConfig()


def at(h, day=5):
    return datetime(2026, 10, day, h, 0, tzinfo=ROME)


def test_quiet_hours_wrap_midnight():
    assert in_quiet_hours(at(23), CFG)
    assert in_quiet_hours(at(3), CFG)
    assert not in_quiet_hours(at(12), CFG)


def test_decide_levels():
    assert decide(PolicyInput(1, at(12), 0), CFG) == "ignore"
    assert decide(PolicyInput(4, at(12), 0), CFG) == "log"
    assert decide(PolicyInput(6, at(12), 0), CFG) == "notify"
    assert decide(PolicyInput(8, at(12), 0), CFG) == "call"


def test_call_downgraded_by_limits():
    assert decide(PolicyInput(9, at(12), 3), CFG) == "notify"
    assert decide(PolicyInput(9, at(2), 0), CFG) == "notify"
    assert decide(PolicyInput(10, at(2), 0), CFG) == "call"


def test_mute_rule_wins_over_urgency():
    assert decide(PolicyInput(10, at(12), 0, rule="mute"), CFG) == "log"


def test_always_call_rule_promotes_medium_urgency():
    assert decide(PolicyInput(6, at(12), 0), CFG) == "notify"
    assert decide(PolicyInput(6, at(12), 0, rule="always_call"), CFG) == "call"
    # resta soggetta ai guardrail: di notte no
    assert decide(PolicyInput(6, at(3), 0, rule="always_call"), CFG) == "notify"


def test_explicit_rule_beats_a_low_triage_score():
    """Se Andrea ha detto 'i bonifici sono sempre importanti', la regola vale
    anche quando il triage sottovaluta l'evento."""
    assert decide(PolicyInput(3, at(12), 0), CFG) == "log"
    assert decide(PolicyInput(3, at(12), 0, rule="always_call"), CFG) == "call"
    assert decide(PolicyInput(3, at(12), 0, rule="always_notify"), CFG) == "notify"


def test_no_rule_can_promote_noise():
    # urgency 0 è anche il valore di ripiego quando il triage fallisce
    assert decide(PolicyInput(0, at(12), 0, rule="always_call"), CFG) == "ignore"
    assert decide(PolicyInput(2, at(12), 0, rule="always_call"), CFG) == "ignore"


def test_notification_budget_falls_back_to_digest():
    assert decide(PolicyInput(6, at(12), 0, notifications_last_hour=4), CFG) == "digest"
    assert decide(PolicyInput(6, at(12), 0, notifications_last_hour=4, rule="always_notify"), CFG) == "notify"
    # una cosa da telefonata non finisce mai nel digest
    assert decide(PolicyInput(9, at(12), 0, notifications_last_hour=99), CFG) == "call"


def test_match_rule_is_substring_and_case_insensitive():
    rules = [Rule("newsletter", "mute"), Rule("bonifico", "always_call")]
    assert match_rule("email Newsletter settimanale", rules) == "mute"
    assert match_rule("Avviso di BONIFICO", rules) == "always_call"
    assert match_rule("call con Paolo", rules) is None


def test_retry_at_defers_night_call_to_morning():
    when = retry_at(PolicyInput(9, at(3), 0), CFG)
    assert when is not None
    assert (when.astimezone(ROME).hour, when.astimezone(ROME).day) == (8, 5)


def test_retry_at_defers_capped_call_to_tomorrow():
    when = retry_at(PolicyInput(9, at(12), 3), CFG)
    assert when.astimezone(ROME).day == 6 and when.astimezone(ROME).hour == 0


def test_retry_at_none_when_no_call_was_wanted():
    assert retry_at(PolicyInput(6, at(12), 0), CFG) is None
    assert retry_at(PolicyInput(9, at(12), 0), CFG) is None   # la chiamata parte subito
