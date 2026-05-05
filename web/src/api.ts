const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

function wsUrl(): string {
  const base = API_BASE_URL || window.location.origin;
  return base.replace(/^http/, "ws");
}

export type Phase = "uploading" | "analyzing" | "clipping" | "done" | "error";

export interface AnalyzerDetail {
  stage: number;
  stage_total: number;
  frames_done: number;
  frames_total: number;
  started_at: number | null;
}

export interface HighlightSegment {
  start_seconds: number;
  end_seconds: number;
  peak_intensity: number;
  description: string;
}

export interface ProgressUpdate {
  phase: Phase;
  percent?: number;
  downloadUrl?: string;
  message?: string;
  analyzerDetail?: AnalyzerDetail;
  jobId?: string;
  highlights?: HighlightSegment[];
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

        if (data.phase === "analyzing") {
          const detail: AnalyzerDetail | undefined = data.analyzer_progress
            ? {
                stage: data.analyzer_progress.stage,
                stage_total: data.analyzer_progress.stage_total,
                frames_done: data.analyzer_progress.frames_done,
                frames_total: data.analyzer_progress.frames_total,
                started_at: data.started_at,
              }
            : undefined;
          onProgress({ phase: "analyzing", analyzerDetail: detail });
        } else if (data.phase === "clipping") {
          onProgress({ phase: "clipping" });
        } else if (data.phase === "completed") {
          clearPendingJob();
          onProgress({
            phase: "done",
            downloadUrl: `${API_BASE_URL}${data.download_url}`,
            highlights: data.highlights,
          });
          return;
        } else if (data.phase === "failed") {
          clearPendingJob();
          onProgress({ phase: "error", message: data.error || "Processing failed" });
          return;
        }

        await new Promise((r) => setTimeout(r, 3000));
      } catch {
        clearPendingJob();
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
): { cancel: () => void } {
  const ws = new WebSocket(`${wsUrl()}/ws/highlight`);
  let jobCreated = false;

  ws.onopen = () => {
    ws.send(JSON.stringify({
      type: "start",
      filename: file.name,
      size: file.size,
    }));

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
        onProgress({ phase: data.phase, percent: data.percent, analyzerDetail: data.detail });
        break;
      case "job_created":
        jobCreated = true;
        localStorage.setItem(STORAGE_KEY, data.job_id);
        break;
      case "done":
        clearPendingJob();
        onProgress({
          phase: "done",
          downloadUrl: `${API_BASE_URL}${data.download_url}`,
          highlights: data.highlights,
        });
        ws.close();
        break;
      case "error":
        clearPendingJob();
        onProgress({ phase: "error", message: data.message });
        ws.close();
        break;
    }
  };

  ws.onerror = () => {
    if (!jobCreated) {
      onProgress({ phase: "error", message: "WebSocket connection failed." });
    }
  };

  ws.onclose = (event) => {
    if (!event.wasClean && !jobCreated) {
      onProgress({ phase: "error", message: "Connection lost." });
    }
  };

  return { cancel: () => ws.close() };
}
