"""FastAPI application serving REST endpoints and the PG-NL2SQL Studio Web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.db.connection import DatabaseManager
from app.db.introspector import SchemaIntrospector
from app.nl2sql.catalog import SchemaCatalog
from app.nl2sql.generator import SQLGenerator
from app.nl2sql.linker import SchemaLinker
from app.nl2sql.pipeline import NL2SQLPipeline


STATIC_DIR = Path(__file__).resolve().parent / "static"


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class QueryRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list)
    max_rows: int | None = Field(default=None, ge=1, le=1000)


class ExecuteRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=20000)
    max_rows: int | None = Field(default=None, ge=1, le=1000)


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = DatabaseManager(
            database_url=settings.database_url,
            statement_timeout_ms=settings.statement_timeout_ms,
        )
        self.introspector = SchemaIntrospector(
            self.db,
            schema_name=settings.schema_name,
            sample_limit=settings.profile_sample_limit,
            low_cardinality_limit=settings.low_cardinality_limit,
            glossary_path=settings.resolved_glossary_path(),
        )
        self.linker = SchemaLinker(
            gemini_api_key=settings.gemini_api_key,
            model_name=settings.gemini_linker_model,
        )
        self.generator = SQLGenerator(
            gemini_api_key=settings.gemini_api_key,
            model_name=settings.gemini_generator_model,
        )
        self.pipeline = NL2SQLPipeline(
            settings=settings,
            db=self.db,
            linker=self.linker,
            generator=self.generator,
        )
        self._catalog: SchemaCatalog | None = None
        self._lock = threading.Lock()

    def get_catalog(self, *, force_refresh: bool = False) -> SchemaCatalog:
        with self._lock:
            if self._catalog is None or force_refresh:
                self._catalog = self.introspector.introspect()
            return self._catalog


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    state = AppState(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        state.db.open()
        try:
            state.get_catalog(force_refresh=True)
        except Exception:
            # Allow app to boot even if DB is temporarily unreachable; /api/health reports status
            pass
        yield
        state.db.close()

    app = FastAPI(
        title="PG-NL2SQL Studio",
        description="PostgreSQL Text-to-SQL engine powered by Gemini, sqlglot AST safety gate, and psycopg3.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.ctx = state

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def serve_index():
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Web UI assets not found.")
        return FileResponse(index_path)

    @app.get("/api/download-bundle")
    def download_bundle():
        bundle_path = Path("/home/antigravity/artifacts/pg-nl2sql.zip")
        if not bundle_path.exists():
            raise HTTPException(status_code=404, detail="Export archive pg-nl2sql.zip not found.")
        return FileResponse(
            bundle_path,
            media_type="application/zip",
            filename="pg-nl2sql.zip",
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        db_status: dict[str, Any]
        try:
            db_status = state.db.check_health()
        except Exception as exc:
            db_status = {"connected": False, "error": str(exc)}

        catalog_tables = len(state._catalog.tables) if state._catalog else 0
        return {
            "status": "ok" if db_status.get("connected") else "degraded",
            "mode": "gemini" if cfg.has_gemini else "demo",
            "models": {
                "linker": cfg.gemini_linker_model if cfg.has_gemini else "heuristic",
                "generator": cfg.gemini_generator_model if cfg.has_gemini else "demo_heuristic",
            },
            "database": db_status,
            "catalog_tables": catalog_tables,
            "limits": {
                "default_row_limit": cfg.default_row_limit,
                "max_row_limit": cfg.max_row_limit,
                "statement_timeout_ms": cfg.statement_timeout_ms,
            },
        }

    @app.get("/api/schema")
    def get_schema() -> dict[str, Any]:
        try:
            catalog = state.get_catalog()
            return catalog.to_api_dict()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Schema introspection failed: {exc}") from exc

    @app.post("/api/schema/refresh")
    def refresh_schema() -> dict[str, Any]:
        try:
            catalog = state.get_catalog(force_refresh=True)
            return {
                "refreshed_at": catalog.refreshed_at,
                "table_count": len(catalog.tables),
                "enum_count": len(catalog.enums),
                "foreign_key_count": len(catalog.foreign_keys),
                "catalog": catalog.to_api_dict(),
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Schema refresh failed: {exc}") from exc

    @app.post("/api/query")
    def nl_query(req: QueryRequest) -> dict[str, Any]:
        try:
            catalog = state.get_catalog()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Database schema unavailable: {exc}") from exc

        result = state.pipeline.run_query(
            prompt=req.prompt,
            catalog=catalog,
            history=[m.model_dump() for m in req.history],
            max_rows=req.max_rows,
        )
        return result.to_dict()

    @app.post("/api/execute")
    def execute_sql(req: ExecuteRequest) -> dict[str, Any]:
        result = state.pipeline.execute_raw_sql(sql=req.sql, max_rows=req.max_rows)
        return result.to_dict()

    return app


app = create_app()
