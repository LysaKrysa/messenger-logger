"""Shared, content-free capture/forward policy for workers and the gateway."""
import math

DEFAULT = {"version": 1, "capture_default": True, "rules": {}}
FORWARD = {"inherit", "all", "deleted", "off"}


def thread_key(value):
    return isinstance(value, str) and 0 < len(value) <= 150 and all(char.isprintable() for char in value)


def valid(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or type(value.get("capture_default")) is not bool:
        return False
    rules = value.get("rules")
    return (isinstance(rules, dict) and len(rules) <= 200 and all(thread_key(key) and isinstance(rule, dict)
            and (rule.get("capture") is None or type(rule.get("capture")) is bool)
            and isinstance(rule.get("forward"), str) and rule["forward"] in FORWARD and type(rule.get("since")) in {int, float}
            and math.isfinite(rule["since"]) and rule["since"] >= 0 for key, rule in rules.items()))


def capture(value, tid):
    rule = value["rules"].get(tid, {})
    return value["capture_default"] if rule.get("capture") is None else rule["capture"]


def forwarding(value, tid, default):
    if not capture(value, tid):
        return "off"
    mode = value["rules"].get(tid, {}).get("forward", "inherit")
    return default if mode == "inherit" else mode


def configured(value):
    return not value["capture_default"] or bool(value["rules"])
