from datetime import datetime

LOOKUPS = {"gt": ">", "gte": ">=", "lte": "<=", "lt": "<", "in": "IN"}


def compile_select(*, fields=(), alias: str = "s", where: dict = None) -> str:
    """
    Compiles a S3 select "SQL" query
    """
    columns = ", ".join([_compile_column(alias, f) for f in fields]) if fields else f"{alias}.*"
    query = f"SELECT {columns} FROM s3object {alias}"

    conditions = []
    if where:
        conditions += [_compile_condition(alias, k, v) for k, v in where.items()]
    if conditions:
        query += f" WHERE {' AND '.join(conditions)}"

    return query


def _compile_condition(alias: str, field: str, val) -> str:
    if field == "__raw__":
        return val

    op = "="
    field_parts = field.split("__")
    if field_parts[-1] in LOOKUPS:
        op = LOOKUPS[field_parts[-1]]
        field = "__".join(field_parts[:-1])

    column = _compile_column(alias, field, cast="TIMESTAMP" if isinstance(val, datetime) else None)
    value = _compile_value(val)

    return f"{column} {op} {value}"


def _compile_column(alias: str, field: str, cast: str = None) -> str:
    col = f"{alias}.{field.replace('__', '.')}"

    if cast:
        col = f"CAST({col} AS {cast})"

    return col


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
