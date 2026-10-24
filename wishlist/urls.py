from werkzeug.routing import IntegerConverter


class BoundedIntConverter(IntegerConverter):
    """`<int:...>` that only matches ids SQLite can store (1..2**63-1); larger ones are a 404."""

    def __init__(self, map, **kwargs):
        kwargs.setdefault("min", 1)
        kwargs.setdefault("max", 2**63 - 1)
        super().__init__(map, **kwargs)
