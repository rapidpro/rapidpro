from datetime import datetime

LOOKUPS = {"gt": ">", "gte": ">=", "lte": "<=", "lt": "<", "in": "IN"}


def compile_select(alias: str = "s", **kwargs) -> str:
    return " AND ".join([_compile_condition(alias, k, v) for k, v in kwargs.items()])


def _compile_condition(alias: str, col: str, val) -> str:
    op = "="
    col_parts = col.split("__")
    if col_parts[-1] in LOOKUPS:
        op = LOOKUPS[col_parts[-1]]
        col_parts = col_parts[:-1]

    col = ".".join(col_parts)
    val = _compile_value(val)

    return f"{alias}.{col} {op} {val}"


def _compile_value(val) -> str:
    if isinstance(val, str):
        return f"'{val}'"
    elif isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    elif isinstance(val, datetime):
        return f"CAST('{val.isoformat()}' AS TIMESTAMP)"
    if isinstance(val, (list, tuple)):
        return f"({', '.join([_compile_value(v) for v in val])})"
    return str(val)
