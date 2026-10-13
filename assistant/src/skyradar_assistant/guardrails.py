"""SQL guardrails for LLM-generated queries.

Defence in depth (each layer is sufficient on its own for the read-only goal):

1. The query is parsed with sqlglot (DuckDB dialect): exactly one statement,
   and it must be a SELECT (CTEs / set operations allowed).
2. Every referenced table must belong to the allow-list; table functions
   (read_parquet, read_csv, glob, ...) and file paths are rejected, so the LLM
   cannot read arbitrary files.
3. A LIMIT is forced (and capped).
4. Execution happens on an in-memory DuckDB connection whose views point to the
   mart Parquet files, with external access disabled and a timeout.
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlglot
from sqlglot import exp

FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Merge,
    exp.Command,
    exp.Copy,
    exp.Pragma,
    exp.Set,
    exp.Use,
    exp.Attach,
    exp.Detach,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
)


class UnsafeQueryError(ValueError):
    """Raised when a generated query violates a guardrail."""


def validate_sql(sql: str, allowed_tables: Iterable[str], max_limit: int = 1000) -> str:
    """Return a safe, normalised version of ``sql`` or raise :class:`UnsafeQueryError`."""
    allowed = {t.lower() for t in allowed_tables}
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQueryError(f"SQL invalide : {exc}") from exc
    if len(statements) != 1:
        raise UnsafeQueryError("Une seule requête est autorisée.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise UnsafeQueryError("Seules les requêtes SELECT sont autorisées.")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeQueryError(f"Instruction interdite : {type(node).__name__}")
        if isinstance(node, exp.Anonymous | exp.ReadCSV | exp.ReadParquet) and _is_table_function(
            node
        ):
            raise UnsafeQueryError("Les fonctions de lecture de fichiers sont interdites.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise UnsafeQueryError("Les fonctions de table et chemins de fichiers sont interdits.")
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeQueryError(f"Référence qualifiée interdite : {table.sql()}")
        if name not in allowed and name not in cte_names:
            raise UnsafeQueryError(f"Table non autorisée : {table.name}")

    limit = tree.args.get("limit")
    current = _limit_value(limit)
    if current is None or current > max_limit:
        tree = (
            tree.limit(max_limit, copy=False)
            if isinstance(tree, exp.Select)
            else (exp.select("*").from_(tree.subquery("q")).limit(max_limit))
        )
    return tree.sql(dialect="duckdb")


def _is_table_function(node: exp.Expression) -> bool:
    name = node.name.lower() if isinstance(node, exp.Anonymous) else type(node).__name__.lower()
    return any(k in name for k in ("read", "glob", "scan", "parquet", "csv", "json"))


def _limit_value(limit: exp.Expression | None) -> int | None:
    if limit is None:
        return None
    expression = limit.args.get("expression")
    if isinstance(expression, exp.Literal) and expression.is_int:
        return int(expression.this)
    return None
