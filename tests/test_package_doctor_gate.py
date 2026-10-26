"""Regression for package smoke when a newer compatible SDK is installed."""
from scripts.check_package_doctor import acceptable


def report(*checks):
    return {"checks": list(checks)}


def check(id, verdict, detail=""):
    return {"id": id, "verdict": verdict, "detail": detail}


def test_all_pass():
    assert acceptable(report(check("python", "pass"), check("a2a_sdk", "pass")))


def test_new_untested_sdk_allowed():
    assert acceptable(report(
        check("python", "pass"),
        check("a2a_sdk", "warn", "a2a-sdk 1.2.4 is inside the declared range >=1.2.0,<1.3 but was not tested"),
    ))


def test_outside_range_or_known_broken_not_allowed():
    assert not acceptable(report(check("a2a_sdk", "fail", "outside the declared range")))
    assert not acceptable(report(check("a2a_sdk", "fail", "known broken")))


def test_other_warnings_and_unknown_block():
    assert not acceptable(report(check("config.warning", "warn", "unsafe setup")))
    assert not acceptable(report(check("backend", "unknown", "offline")))
    assert not acceptable(report())


def test_unrelated_sdk_warning_not_whitelisted():
    assert not acceptable(report(check("a2a_sdk", "warn", "unexpected SDK discrepancy")))
