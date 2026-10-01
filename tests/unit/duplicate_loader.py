"""Loads cps/duplicate_rules.py and cps/duplicate_detection.py under their 'cps.*' names for
tests that exec cps/duplicates.py against stubbed 'cps' modules (there is no real 'cps'
package in those tests, so a relative import needs the module registered first)."""

import importlib.util
import pathlib
import sys


def _load(name):
    path = pathlib.Path(__file__).resolve().parents[2] / "cps" / (name + ".py")
    spec = importlib.util.spec_from_file_location("cps." + name, path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "cps"
    sys.modules["cps." + name] = module
    spec.loader.exec_module(module)
    return module


def load_duplicate_rules():
    """Rules first: duplicate_detection imports from it. Returns the rules module."""
    rules = _load("duplicate_rules")
    _load("duplicate_detection")
    return rules
