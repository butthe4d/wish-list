from werkzeug.routing import IntegerConverter

# largest id SQLite can store
MAX_ID = 2**63 - 1


class BoundedIntConverter(IntegerConverter):
    """`<int:...>` that only matches ids SQLite can store (1..MAX_ID); larger ones are a 404."""

    def __init__(self, map, **kwargs):
        kwargs.setdefault("min", 1)
        kwargs.setdefault("max", MAX_ID)
        super().__init__(map, **kwargs)
