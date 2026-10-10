__all__ = [
    "finalize_and_export",
]


def __getattr__(name: str):
    if name == "finalize_and_export":
        from .finalize import finalize_and_export

        return finalize_and_export
    raise AttributeError(name)
