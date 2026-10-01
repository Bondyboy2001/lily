"""CSRF protection and rate limiting must always be real objects, never silently disabled."""
import cps


def test_csrf_and_limiter_are_always_configured():
    assert cps.csrf is not None
    assert cps.limiter is not None
    assert not hasattr(cps, "wtf_present") and not hasattr(cps, "limiter_present")
