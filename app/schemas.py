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
    updated_at: str | None = None
    services: list[ServiceStatus] = Field(default_factory=list)


# --- Analyzer 関連 ---


class AnalyzerOptions(BaseModel):
    """analyzer に渡すオプションパラメータ（すべて任意）."""

    start: float | None = None
    end: float | None = None
    interval: float = 5.0
    threshold: int = 100
    model: str | None = None
    concurrency: int = 4


class AnalyzerFrameResult(BaseModel):
    """analyzer レスポンス内のフレーム解析結果."""

    timestamp_seconds: float
    score: int = 0
    score_kills: int = 0
    score_count_gain: int = 0
    score_dead: int = 0
    my_team_count: int | None = None
    enemy_team_count: int | None = None
    kills: int = 0
    is_dead: bool = False
    my_team_count_raw: int | None = None
    enemy_team_count_raw: int | None = None


class AnalyzerHighlight(BaseModel):
    """analyzer レスポンス内のハイライト情報."""

    start_seconds: float
    end_seconds: float
    peak_intensity: int = 0


class AnalyzerScoringInfo(BaseModel):
    """analyzer レスポンス内のスコア計算説明."""

    model_config = ConfigDict(extra="allow")

    description: str = ""
    score: str = ""
    score_kills: str = ""
    score_count_gain: str = ""
    score_dead: str = ""
    weights: dict = Field(default_factory=dict)
    death_penalty: float = 0
    count_gain_window_seconds: int = 30


class AnalyzerResponse(BaseModel):
    """analyzer の /analyze/highlights レスポンス（必要フィールドのみ）."""

    model_config = ConfigDict(extra="allow")

    video: str = ""
    model: str = ""
    highlights: list[AnalyzerHighlight] = Field(default_factory=list)
    scoring: AnalyzerScoringInfo = Field(default_factory=AnalyzerScoringInfo)
    frames: list[AnalyzerFrameResult] = Field(default_factory=list)
    scan_summary: dict = Field(default_factory=dict)


# --- Analyzer ジョブ関連 ---


class AnalyzerJobResponse(BaseModel):
    """analyzer の /analyze/highlights/jobs POST レスポンス."""

    job_id: str


class AnalyzerJobProgress(BaseModel):
    """analyzer のジョブ進捗."""

    phase: int = 0
    phase_total: int = 1
    frames_done: int = 0
    frames_total: int = 0


class AnalyzerJobStatus(BaseModel):
    """analyzer の /analyze/highlights/jobs/{job_id} GET レスポンス."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: str
    progress: AnalyzerJobProgress | None = None
    result: AnalyzerResponse | None = None
    error: str | None = None
    started_at: float | None = None


# --- Clipper 関連 ---


class ClipSegment(BaseModel):
    """clipper に渡すセグメント."""

    start: str
    end: str


class ClipperJobResponse(BaseModel):
    """clipper の /clip/jobs POST レスポンス."""

    job_id: str


class ClipperJobStatus(BaseModel):
    """clipper の /clip/jobs/{job_id} GET レスポンス."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: str
    result_path: str | None = None
    error: str | None = None
    started_at: float | None = None


# --- エラー ---


class ErrorResponse(BaseModel):
    """エラーレスポンス."""

    detail: str


# --- オーケストレータージョブ関連 ---


class OrchestratorFrameInfo(BaseModel):
    """フレーム解析結果."""

    timestamp_seconds: float
    score: int = 0
    score_kills: int = 0
    score_count_gain: int = 0
    score_dead: int = 0
    my_team_count: int | None = None
    enemy_team_count: int | None = None
    kills: int = 0
    is_dead: bool = False
    my_team_count_raw: int | None = None
    enemy_team_count_raw: int | None = None


class OrchestratorHighlightInfo(BaseModel):
    """検出されたハイライト区間の情報."""

    start_seconds: float
    end_seconds: float
    peak_intensity: int = 0


class OrchestratorAnalyzerProgress(BaseModel):
    """analyzer の進捗."""

    stage: int = 0
    stage_total: int = 1
    frames_done: int = 0
    frames_total: int = 0


class OrchestratorJobStatusResponse(BaseModel):
    """GET /jobs/{job_id} レスポンス."""

    job_id: str
    phase: str
    analyzer_progress: OrchestratorAnalyzerProgress | None = None
    download_url: str | None = None
    analysis_url: str | None = None
    error: str | None = None
    started_at: float | None = None
