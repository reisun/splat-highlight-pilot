"""Pydantic モデル定義."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# --- ヘルスチェック ---


class ServiceStatus(BaseModel):
    """外部サービスの接続状態."""

    name: str
    status: str = Field(description="connected / disconnected")
    detail: str | None = None


class HealthResponse(BaseModel):
    """ヘルスチェックレスポンス."""

    status: str = "ok"
    services: list[ServiceStatus] = Field(default_factory=list)


# --- Analyzer 関連 ---


class AnalyzerOptions(BaseModel):
    """analyzer に渡すオプションパラメータ（すべて任意）."""

    start: float | None = None
    end: float | None = None
    stage1_interval: int = 30
    stage2_interval: int = 5
    threshold: int = 5
    max_highlights: int | None = None
    model: str | None = None
    concurrency: int = 1


class AnalyzerHighlight(BaseModel):
    """analyzer レスポンス内のハイライト情報."""

    start_time: float
    end_time: float
    description: str | None = None
    score: float | None = None


class AnalyzerResponse(BaseModel):
    """analyzer の /analyze/highlights レスポンス（必要フィールドのみ）."""

    model_config = ConfigDict(extra="allow")

    highlights: list[AnalyzerHighlight] = Field(default_factory=list)


# --- Clipper 関連 ---


class ClipSegment(BaseModel):
    """clipper に渡すセグメント."""

    start: str
    end: str


# --- エラー ---


class ErrorResponse(BaseModel):
    """エラーレスポンス."""

    detail: str
