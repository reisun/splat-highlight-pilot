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

export interface ProgressUpdate {
  phase: Phase;
  percent?: number;
  downloadUrl?: string;
  message?: string;
  analyzerDetail?: AnalyzerDetail;
}

const CHUNK_SIZE = 1024 * 1024;

export function createHighlight(
  file: File,
  onProgress: (update: ProgressUpdate) => void,
): { cancel: () => void } {
  const ws = new WebSocket(`${wsUrl()}/ws/highlight`);

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
      case "done":
        onProgress({
          phase: "done",
          downloadUrl: `${API_BASE_URL}${data.download_url}`,
        });
        ws.close();
        break;
      case "error":
        onProgress({ phase: "error", message: data.message });
        ws.close();
        break;
    }
  };

  ws.onerror = () => {
    onProgress({ phase: "error", message: "WebSocket connection failed." });
  };

  ws.onclose = (event) => {
    if (!event.wasClean) {
      onProgress({ phase: "error", message: "Connection lost." });
    }
  };

  return { cancel: () => ws.close() };
}
