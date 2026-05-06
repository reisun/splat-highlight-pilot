import { useState, useCallback, useRef, useEffect } from "react";
import Header from "./components/Header";
import DropZone from "./components/DropZone";
import Processing from "./components/Processing";
import ResultView from "./components/ResultView";
import ErrorMessage from "./components/ErrorMessage";
import { createHighlight, resumeJob, getPendingJobId, clearPendingJob, type ProgressUpdate, type AnalyzerDetail } from "./api";

type AppState =
  | { phase: "idle" }
  | { phase: "uploading"; fileName: string; percent: number }
  | { phase: "analyzing"; fileName: string; analyzerDetail?: AnalyzerDetail }
  | { phase: "clipping"; fileName: string }
  | { phase: "done"; downloadUrl: string; analysisUrl?: string }
  | { phase: "error"; message: string };

export default function App() {
  const [state, setState] = useState<AppState>({ phase: "idle" });
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    const pendingJobId = getPendingJobId();
    if (!pendingJobId) return;

    setState({ phase: "analyzing", fileName: "Resuming..." });

    const { cancel } = resumeJob(pendingJobId, (update: ProgressUpdate) => {
      switch (update.phase) {
        case "analyzing":
          setState({ phase: "analyzing", fileName: "Resuming...", analyzerDetail: update.analyzerDetail });
          break;
        case "clipping":
          setState({ phase: "clipping", fileName: "Resuming..." });
          break;
        case "done":
          cancelRef.current = null;
          setState({ phase: "done", downloadUrl: update.downloadUrl ?? "", analysisUrl: update.analysisUrl });
          break;
        case "error":
          cancelRef.current = null;
          setState({ phase: "error", message: update.message ?? "An unexpected error occurred." });
          break;
      }
    });

    cancelRef.current = cancel;
  }, []);

  const handleFileSelected = useCallback((file: File) => {
    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
    }

    setState({ phase: "uploading", fileName: file.name, percent: 0 });

    const { cancel } = createHighlight(file, (update: ProgressUpdate) => {
      switch (update.phase) {
        case "uploading":
          setState({
            phase: "uploading",
            fileName: file.name,
            percent: update.percent ?? 0,
          });
          break;
        case "analyzing":
          setState({ phase: "analyzing", fileName: file.name, analyzerDetail: update.analyzerDetail });
          break;
        case "clipping":
          setState({ phase: "clipping", fileName: file.name });
          break;
        case "done":
          cancelRef.current = null;
          setState({ phase: "done", downloadUrl: update.downloadUrl ?? "", analysisUrl: update.analysisUrl });
          break;
        case "error":
          cancelRef.current = null;
          setState({
            phase: "error",
            message: update.message ?? "An unexpected error occurred.",
          });
          break;
      }
    });

    cancelRef.current = cancel;
  }, []);

  const handleReset = useCallback(() => {
    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
    }
    clearPendingJob();
    setState({ phase: "idle" });
  }, []);

  return (
    <div className="min-h-screen bg-gray-100">
      <Header />
      <main className="max-w-2xl mx-auto px-4 py-8">
        {state.phase === "idle" && (
          <DropZone onFileSelected={handleFileSelected} disabled={false} />
        )}
        {(state.phase === "uploading" ||
          state.phase === "analyzing" ||
          state.phase === "clipping") && (
          <Processing
            phase={state.phase}
            fileName={state.fileName}
            percent={state.phase === "uploading" ? state.percent : undefined}
            analyzerDetail={state.phase === "analyzing" ? state.analyzerDetail : undefined}
          />
        )}
        {state.phase === "done" && (
          <ResultView
            downloadUrl={state.downloadUrl}
            analysisUrl={state.analysisUrl}
            onReset={handleReset}
          />
        )}
        {state.phase === "error" && (
          <ErrorMessage message={state.message} onRetry={handleReset} />
        )}
      </main>
    </div>
  );
}
