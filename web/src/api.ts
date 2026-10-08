import { getApiBaseUrl } from "./config";

function wsUrl(): string {
  const base = getApiBaseUrl();
  return base.replace(/^http/, "ws");
}

export type Phase = "uploading" | "scanning" | "analyzing" | "clipping" | "done" | "error";

export interface AnalyzerDetail {
  stage: number;
  stage_total: number;
  frames_done: number;
  frames_total: number;
  started_at: number | null;
}

export interface MatchDetail {
  current_match: number;
  total_matches: number;
}

export interface ProgressUpdate {
  phase: Phase;
  percent?: number;
  downloadUrl?: string;
  analysisUrl?: string;
  message?: string;
  analyzerDetail?: AnalyzerDetail;
  matchDetail?: MatchDetail;
  jobId?: string;
}

export interface HighlightOptions {
  weights?: Record<string, number>;
  per_match?: boolean;
}

const CHUNK_SIZE = 1024 * 1024;
const MAX_UPLOAD_BYTES = 15_000_000_000;
const STORAGE_KEY = "splat-highlight-job-id";

export function getPendingJobId(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

export function clearPendingJob(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function resumeJob(
  jobId: string,
  onProgress: (update: ProgressUpdate) => void,
): { cancel: () => void } {
  const API = getApiBaseUrl();
  let cancelled = false;

  const poll = async () => {
    while (!cancelled) {
      try {
        const resp = await fetch(`${API}/jobs/${jobId}`);
        if (!resp.ok) {
          clearPendingJob();
          onProgress({ phase: "error", message: `Job not found (${resp.status})` });
          return;
        }
        const data = await resp.json();

        const matchDetail: MatchDetail | undefined = data.match_progress
          ? {
              current_match: data.match_progress.current_match,
              total_matches: data.match_progress.total_matches,
            }
          : undefined;

        if (data.phase === "scanning") {
          const detail: AnalyzerDetail | undefined = data.analyzer_progress
            ? {
                stage: data.analyzer_progress.stage,
                stage_total: data.analyzer_progress.stage_total,
                frames_done: data.analyzer_progress.frames_done,
                frames_total: data.analyzer_progress.frames_total,
                started_at: data.started_at,
              }
            : undefined;
          onProgress({ phase: "scanning", analyzerDetail: detail });
        } else if (data.phase === "analyzing") {
          const detail: AnalyzerDetail | undefined = data.analyzer_progress
            ? {
                stage: data.analyzer_progress.stage,
                stage_total: data.analyzer_progress.stage_total,
                frames_done: data.analyzer_progress.frames_done,
                frames_total: data.analyzer_progress.frames_total,
                started_at: data.started_at,
              }
            : undefined;
          onProgress({ phase: "analyzing", analyzerDetail: detail, matchDetail });
        } else if (data.phase === "clipping") {
          onProgress({ phase: "clipping", matchDetail });
        } else if (data.phase === "completed") {
          onProgress({
            phase: "done",
            downloadUrl: `${getApiBaseUrl()}${data.download_url}`,
            analysisUrl: data.analysis_url ? `${getApiBaseUrl()}${data.analysis_url}` : undefined,
          });
          return;
        } else if (data.phase === "failed") {
          onProgress({ phase: "error", message: data.error || "Processing failed" });
          return;
        }

        await new Promise((r) => setTimeout(r, 3000));
      } catch {
        onProgress({ phase: "error", message: "Failed to check job status" });
        return;
      }
    }
  };

  poll();
  return { cancel: () => { cancelled = true; clearPendingJob(); } };
}

export function createHighlight(
  file: File,
  onProgress: (update: ProgressUpdate) => void,
  options?: HighlightOptions,
): { cancel: () => void } {
  if (file.size > MAX_UPLOAD_BYTES || file.size === 0) {
    onProgress({
      phase: "error",
      message: file.size === 0 ? "空の動画はアップロードできません。" : "動画の上限は15GBです。",
    });
    return { cancel: () => {} };
  }

  const ws = new WebSocket(`${wsUrl()}/ws/upload`);
  let pollCancel: (() => void) | null = null;
  let stopped = false;
  let admitted = false;
  let offset = 0;
  let awaitingAck = false;
  let reading = false;
  let uploadCompleteSent = false;

  const fail = (message: string) => {
    if (stopped || pollCancel) return;
    stopped = true;
    onProgress({ phase: "error", message });
    ws.close();
  };

  const sendNext = async () => {
    if (stopped || !admitted || awaitingAck || reading || uploadCompleteSent || ws.readyState !== WebSocket.OPEN) return;
    if (offset >= file.size) {
      uploadCompleteSent = true;
      ws.send(JSON.stringify({ type: "upload_complete" }));
      return;
    }
    reading = true;
    try {
      const buf = await file.slice(offset, offset + CHUNK_SIZE).arrayBuffer();
      if (stopped || ws.readyState !== WebSocket.OPEN) return;
      awaitingAck = true;
      ws.send(buf);
      offset += buf.byteLength;
    } catch {
      fail("動画の読み込み・送信に失敗しました。");
    } finally {
      reading = false;
    }
  };

  ws.onopen = () => {
    if (stopped) {
      ws.close();
      return;
    }
    const startMsg: Record<string, unknown> = {
      type: "start",
      filename: file.name,
      size: file.size,
    };
    if (options) startMsg.options = options;
    ws.send(JSON.stringify(startMsg));
  };

  ws.onmessage = (event) => {
    if (stopped) return;
    try {
      const data = JSON.parse(event.data as string);
      switch (data.type) {
        case "ready":
          if (!admitted) {
            admitted = true;
            void sendNext();
          }
          break;
        case "progress":
          onProgress({ phase: data.phase, percent: data.percent });
          if (data.phase === "uploading" && awaitingAck) {
            awaitingAck = false;
            void sendNext();
          }
          break;
        case "busy": {
          let message = data.message || "現在処理中です。時間をおいて再度アクセスしてください。";
          const finish = data.estimated_finish_at ? new Date(data.estimated_finish_at) : null;
          if (finish && !Number.isNaN(finish.getTime())) {
            message += ` 推定終了時刻: ${finish.toLocaleString("ja-JP")}（目安）`;
          }
          fail(message);
          break;
        }
        case "job_created": {
          if (pollCancel) break;
          const jobId = data.job_id as string;
          localStorage.setItem(STORAGE_KEY, jobId);
          onProgress({ phase: "scanning", jobId });
          const { cancel } = resumeJob(jobId, onProgress);
          pollCancel = cancel;
          break;
        }
        case "error":
          fail(data.message || "アップロードに失敗しました。");
          break;
      }
    } catch {
      fail("サーバーからの応答を読み取れませんでした。");
    }
  };

  ws.onerror = () => fail("WebSocket接続に失敗しました。");
  ws.onclose = () => fail("接続が切断されました。再度アクセスしてください。");

  return {
    cancel: () => {
      stopped = true;
      ws.close();
      pollCancel?.();
    },
  };
}
