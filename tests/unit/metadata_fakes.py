"""A stand-in metadata provider for the lookup tests."""
from cps.services.Metadata import Metadata


class FakeProvider(Metadata):
    """A provider answering as the test says: FakeProvider(__id__="google", __name__="Google",
    identifier_types=frozenset(), search=lambda query, *a: [record]). What it isn't given
    behaves as the base class does."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)
