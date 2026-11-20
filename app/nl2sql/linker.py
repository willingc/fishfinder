"""Stage 1 Schema Linker (Gemini Flash / heuristic fallback) + Stage 2 FK Graph Expander."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
from typing import Any

from pydantic import BaseModel, Field

from app.nl2sql.catalog import ForeignKeyInfo, SchemaCatalog


class _GeminiLinkResponse(BaseModel):
    tables: list[str] = Field(description="Exact table or view names relevant to the question.")
    reasoning: str = Field(description="Brief explanation of why these tables were chosen.")


@dataclass
class LinkResult:
    seed_tables: list[str]
    expanded_tables: list[str]
    added_bridge_tables: list[str] = field(default_factory=list)
    join_paths: list[str] = field(default_factory=list)
    method: str = "heuristic"
    reasoning: str = ""


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9_]+", text.lower()))
    expanded: set[str] = set()
    for tok in tokens:
        expanded.add(tok)
        # Naive singular/plural normalization for robust lexical linking
        if tok.endswith("ies") and len(tok) > 4:
            expanded.add(tok[:-3] + "y")
        elif tok.endswith("s") and len(tok) > 3:
            expanded.add(tok[:-1])
        else:
            expanded.add(tok + "s")
    return expanded


def expand_fk_graph(
    catalog: SchemaCatalog,
    seed_tables: list[str],
    *,
    max_hops: int = 5,
    max_total_tables: int = 12,
) -> tuple[list[str], list[str], list[str]]:
    """
    Stage 2 FK Graph Expander:
    Finds shortest FK paths between every pair of seed tables to automatically pull in
    intermediate bridge/junction tables (e.g. order_items, product_tags, subscription_addons)
    so the SQL generator never hallucinates multi-hop join keys.
    """
    valid_seeds = [t for t in dict.fromkeys(seed_tables) if t in catalog.tables]
    if len(valid_seeds) <= 1:
        return valid_seeds, [], []

    graph = catalog.build_fk_graph()
    expanded: list[str] = list(valid_seeds)
    expanded_set = set(valid_seeds)
    added_bridges: list[str] = []
    join_paths: list[str] = []

    def _shortest_path(start: str, goal: str) -> list[str] | None:
        if start == goal:
            return [start]
        queue: deque[list[str]] = deque([[start]])
        visited = {start}
        while queue:
            path = queue.popleft()
            if len(path) - 1 >= max_hops:
                continue
            current = path[-1]
            # Prefer stepping through junction/bridge tables first on ties
            neighbors = sorted(
                graph.get(current, {}).keys(),
                key=lambda n: (0 if catalog.tables[n].is_junction_table else 1, n),
            )
            for nxt in neighbors:
                if nxt == goal:
                    return path + [nxt]
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(path + [nxt])
        return None

    for i, src in enumerate(valid_seeds):
        for dst in valid_seeds[i + 1 :]:
            path = _shortest_path(src, dst)
            if not path:
                continue
            if len(path) > 2:
                join_paths.append(" -> ".join(path))
            for intermediate in path[1:-1]:
                if intermediate not in expanded_set and len(expanded) < max_total_tables:
                    expanded.append(intermediate)
                    expanded_set.add(intermediate)
                    added_bridges.append(intermediate)

    return expanded, added_bridges, list(dict.fromkeys(join_paths))


def heuristic_link_tables(
    catalog: SchemaCatalog,
    prompt: str,
    history: list[dict[str, str]] | None = None,
    *,
    max_seed_tables: int = 6,
) -> tuple[list[str], str]:
    """Deterministic lexical + glossary scorer over the schema catalog (offline fallback)."""
    combined_text = prompt
    if history:
        recent_user = " ".join(
            msg.get("content", "") for msg in history[-3:] if msg.get("role") == "user"
        )
        combined_text = f"{recent_user} {prompt}".strip()

    query_tokens = _tokenize(combined_text)
    lower_text = combined_text.lower()

    scores: dict[str, float] = {name: 0.0 for name in catalog.tables}

    # Domain keyword hints for common business language
    domain_hints: dict[str, list[str]] = {
        "mrr": ["subscriptions", "plans", "organizations", "subscription_addons", "addons", "v_active_customer_mrr"],
        "revenue": ["orders", "order_items", "regions", "v_monthly_revenue_summary"],
        "invoice": ["invoices", "payments", "organizations"],
        "overdue": ["invoices", "organizations"],
        "payment": ["payments", "invoices"],
        "ticket": ["support_tickets", "customers", "organizations"],
        "sla": ["support_tickets", "customers"],
        "audit": ["audit_logs", "customers"],
        "ip": ["audit_logs", "customers"],
        "warehouse": ["warehouses", "inventory_lots", "products", "regions"],
        "inventory": ["inventory_lots", "warehouses", "products"],
        "stock": ["inventory_lots", "warehouses", "products"],
        "tag": ["tags", "product_tags", "products", "customers"],
        "soc2": ["products", "tags", "organizations"],
        "hipaa": ["products", "tags", "addons", "organizations", "support_tickets"],
        "enterprise": ["customers", "organizations", "plans"],
        "onboarding": ["customers", "organizations"],
        "addon": ["addons", "subscription_addons", "subscriptions"],
        "add-on": ["addons", "subscription_addons", "subscriptions"],
    }
    for kw, hinted_tables in domain_hints.items():
        if kw in lower_text:
            for tbl_name in hinted_tables:
                if tbl_name in scores:
                    scores[tbl_name] += 4.5

    # Score via glossary terms
    for term in catalog.glossary.terms:
        if term.name.lower() in lower_text:
            for tbl_name in term.tables:
                if tbl_name in scores:
                    scores[tbl_name] += 5.0

    # Score tables by name, comment, column names, enums, and JSONB keys
    for name, tbl in catalog.tables.items():
        name_tokens = _tokenize(name.replace("_", " "))
        scores[name] += 6.0 * len(query_tokens & name_tokens)

        if tbl.comment:
            scores[name] += 1.2 * len(query_tokens & _tokenize(tbl.comment))

        for col in tbl.columns:
            col_tokens = _tokenize(col.name.replace("_", " "))
            scores[name] += 2.0 * len(query_tokens & col_tokens)
            for ev in col.enum_values:
                if ev.lower() in lower_text:
                    scores[name] += 2.5
            for dv in col.distinct_values:
                if len(dv) >= 2 and re.search(rf"\b{re.escape(dv.lower())}\b", lower_text):
                    scores[name] += 2.0
            for jpath in col.jsonb_schema:
                last_key = jpath.split(".")[-1].replace("[]", "")
                if last_key.lower() in query_tokens:
                    scores[name] += 1.8

        # Slightly prefer base tables over views unless view is explicitly mentioned
        if tbl.table_type == "view" and name not in lower_text:
            scores[name] *= 0.8

    ranked = sorted(
        [(tbl_name, sc) for tbl_name, sc in scores.items() if sc > 0],
        key=lambda item: (-item[1], item[0]),
    )
    selected = [name for name, _ in ranked[:max_seed_tables]]

    if not selected:
        # Sensible default core tables if prompt is very generic
        selected = [t for t in ("customers", "organizations", "orders", "products") if t in catalog.tables]

    reasoning = "Matched tables via lexical, column, enum, JSONB key, and glossary relevance."
    return selected, reasoning


class SchemaLinker:
    """Two-stage schema linker: Gemini Flash (or heuristic fallback) + FK graph expansion."""

    def __init__(self, *, gemini_api_key: str = "", model_name: str = "gemini-2.5-flash") -> None:
        self.gemini_api_key = gemini_api_key.strip()
        self.model_name = model_name
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.gemini_api_key)
        return self._client

    def link(
        self,
        catalog: SchemaCatalog,
        prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> LinkResult:
        """Select candidate tables and expand along the FK graph to include bridge tables."""
        method = "heuristic"
        if self.gemini_api_key:
            try:
                seed_tables, reasoning = self._link_with_gemini(catalog, prompt, history)
                method = f"gemini:{self.model_name}"
            except Exception as exc:
                seed_tables, reasoning = heuristic_link_tables(catalog, prompt, history)
                reasoning = f"Gemini linker unavailable ({exc}); fell back to heuristic. {reasoning}"
        else:
            seed_tables, reasoning = heuristic_link_tables(catalog, prompt, history)

        expanded, added_bridges, join_paths = expand_fk_graph(catalog, seed_tables)
        return LinkResult(
            seed_tables=seed_tables,
            expanded_tables=expanded,
            added_bridge_tables=added_bridges,
            join_paths=join_paths,
            method=method,
            reasoning=reasoning,
        )

    def _link_with_gemini(
        self,
        catalog: SchemaCatalog,
        prompt: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[list[str], str]:
        from google.genai import types

        client = self._get_client()
        glossary_summary = "\n".join(
            f"- {t.name}: {t.definition} (tables: {', '.join(t.tables)})"
            for t in catalog.glossary.terms
        )
        history_block = ""
        if history:
            turns = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-4:]]
            history_block = "Recent conversation:\n" + "\n".join(turns) + "\n\n"

        system_prompt = (
            "You are a PostgreSQL schema linker. Given a compact table index and business glossary, "
            "return ONLY the exact table or view names required to answer the user's question (typically 2-8 tables). "
            "Prefer high recall so no needed table is omitted; bridge tables will be expanded automatically."
        )
        user_content = (
            f"{history_block}"
            f"User Question: {prompt}\n\n"
            f"Schema Compact Index:\n{catalog.compact_index()}\n\n"
            f"Business Glossary:\n{glossary_summary or '(none)'}"
        )

        response = client.models.generate_content(
            model=self.model_name,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=_GeminiLinkResponse,
                temperature=0.0,
            ),
        )
        parsed = _GeminiLinkResponse.model_validate_json(response.text)
        valid_tables = [t for t in parsed.tables if t in catalog.tables]
        if not valid_tables:
            return heuristic_link_tables(catalog, prompt, history)
        return valid_tables, parsed.reasoning
