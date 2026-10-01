"""Loads cps/duplicate_rules.py under the name 'cps.duplicate_rules' for tests that exec
cps/duplicates.py or cps/duplicate_index.py against stubbed 'cps' modules (there is no real
'cps' package in those tests, so a relative import needs the module registered first)."""

import importlib.util
import pathlib
import sys


def load_duplicate_rules():
    path = pathlib.Path(__file__).resolve().parents[2] / "cps" / "duplicate_rules.py"
    spec = importlib.util.spec_from_file_location("cps.duplicate_rules", path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "cps"
    sys.modules["cps.duplicate_rules"] = module
    spec.loader.exec_module(module)
    return module
