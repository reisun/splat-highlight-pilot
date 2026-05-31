const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

function wsUrl(): string {
  const base = API_BASE_URL || window.location.origin;
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
  const API = API_BASE_URL || window.location.origin;
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
            downloadUrl: `${API_BASE_URL}${data.download_url}`,
            analysisUrl: data.analysis_url ? `${API_BASE_URL}${data.analysis_url}` : undefined,
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
  const ws = new WebSocket(`${wsUrl()}/ws/upload`);
  let pollCancel: (() => void) | null = null;

  ws.onopen = () => {
    const startMsg: Record<string, unknown> = {
      type: "start",
      filename: file.name,
      size: file.size,
    };
    if (options) {
      startMsg.options = options;
    }
    ws.send(JSON.stringify(startMsg));

    let offset = 0;
    const sendNext = () => {
      if (offset >= file.size) {
        ws.send(JSON.stringify({ type: "upload_complete" }));
        return;
      }
      const chunk = file.slice(offset, offset + CHUNK_SIZE);
      chunk.arrayBuffer().then((buf) => {
        ws.send(buf);
        offset += buf.byteLength;
        setTimeout(sendNext, 0);
      });
    };
    sendNext();
  };

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data as string);
    switch (data.type) {
      case "progress":
        onProgress({ phase: data.phase, percent: data.percent });
        break;
      case "job_created": {
        const jobId = data.job_id as string;
        localStorage.setItem(STORAGE_KEY, jobId);
        onProgress({ phase: "scanning", jobId });
        const { cancel } = resumeJob(jobId, onProgress);
        pollCancel = cancel;
        break;
      }
      case "error":
        onProgress({ phase: "error", message: data.message });
        ws.close();
        break;
    }
  };

  ws.onerror = () => {
    if (!pollCancel) {
      onProgress({ phase: "error", message: "WebSocket connection failed." });
    }
  };

  ws.onclose = (event) => {
    if (!event.wasClean && !pollCancel) {
      onProgress({ phase: "error", message: "Connection lost." });
    }
  };

  return {
    cancel: () => {
      ws.close();
      pollCancel?.();
    },
  };
}
