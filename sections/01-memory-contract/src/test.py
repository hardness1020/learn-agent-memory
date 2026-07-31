"""Memory contract offline checks: scope identity, structural conformance.

    python sections/01-memory-contract/src/test.py
"""
from contract import MemoryEngine, Scope


class _Toy:
    """The smallest thing that satisfies the contract."""

    def __init__(self):
        self.events = []

    def observe(self, scope, event_type, content):
        self.events.append((scope, content))
        return str(len(self.events))

    def recall(self, scope, query):
        return "\n".join(c for s, c in self.events if s == scope and query in c)

    def consolidate(self, scope):
        return {"merged": 0}


def test():
    # scope is identity: frozen, comparable, usable as a key
    a, b = Scope("acme", "marcus"), Scope("acme", "marcus")
    assert a == b and len({a, b}) == 1
    assert Scope("acme") != Scope("globex")
    try:
        a.tenant_id = "globex"
        raise AssertionError("scope must be immutable")
    except AttributeError:          # FrozenInstanceError is an AttributeError
        pass

    # conformance is structural: no base class, just the three verbs
    toy = _Toy()
    assert isinstance(toy, MemoryEngine)
    assert not isinstance(object(), MemoryEngine)

    # the three verbs in one breath: raw in, scope-filtered out
    toy.observe(a, "user_message", "staging runs in eu-west")
    toy.observe(Scope("globex"), "user_message", "staging runs in us-east")
    assert toy.recall(a, "staging") == "staging runs in eu-west"
    assert toy.consolidate(a) == {"merged": 0}

    print("00 memory-contract: ok")


if __name__ == "__main__":
    test()
