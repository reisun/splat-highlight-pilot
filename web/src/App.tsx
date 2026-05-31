import { useState, useCallback, useRef, useEffect } from "react";
import Header from "./components/Header";
import DropZone from "./components/DropZone";
import OptionsPanel from "./components/OptionsPanel";
import Processing from "./components/Processing";
import ResultView from "./components/ResultView";
import ErrorMessage from "./components/ErrorMessage";
import { createHighlight, resumeJob, getPendingJobId, clearPendingJob, type ProgressUpdate, type AnalyzerDetail, type MatchDetail, type HighlightOptions } from "./api";

type AppState =
  | { phase: "idle" }
  | { phase: "uploading"; fileName: string; percent: number }
  | { phase: "scanning"; fileName: string; analyzerDetail?: AnalyzerDetail }
  | { phase: "analyzing"; fileName: string; analyzerDetail?: AnalyzerDetail; matchDetail?: MatchDetail }
  | { phase: "clipping"; fileName: string; matchDetail?: MatchDetail }
  | { phase: "done"; downloadUrl: string; analysisUrl?: string }
  | { phase: "error"; message: string };

export default function App() {
  const [state, setState] = useState<AppState>({ phase: "idle" });
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [spectatorMode, setSpectatorMode] = useState(false);
  const [perMatch, setPerMatch] = useState(false);
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    const pendingJobId = getPendingJobId();
    if (!pendingJobId) return;

    setState({ phase: "scanning", fileName: "Resuming..." });

    const { cancel } = resumeJob(pendingJobId, (update: ProgressUpdate) => {
      switch (update.phase) {
        case "scanning":
          setState({ phase: "scanning", fileName: "Resuming...", analyzerDetail: update.analyzerDetail });
          break;
        case "analyzing":
          setState({ phase: "analyzing", fileName: "Resuming...", analyzerDetail: update.analyzerDetail, matchDetail: update.matchDetail });
          break;
        case "clipping":
          setState({ phase: "clipping", fileName: "Resuming...", matchDetail: update.matchDetail });
          break;
        case "done":
          cancelRef.current = null;
          setState({ phase: "done", downloadUrl: update.downloadUrl ?? "", analysisUrl: update.analysisUrl });
          break;
        case "error":
          cancelRef.current = null;
          setState({ phase: "error", message: update.message ?? "予期しないエラーが発生しました" });
          break;
      }
    });

    cancelRef.current = cancel;
  }, []);

  const handleFileDrop = useCallback((file: File) => {
    setSelectedFile(file);
  }, []);

  const handleStart = useCallback(() => {
    if (!selectedFile) return;
    const file = selectedFile;

    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
    }

    setState({ phase: "uploading", fileName: file.name, percent: 0 });

    const options: HighlightOptions = {};
    if (spectatorMode) {
      options.weights = { score_count_gain: 0 };
    }
    if (perMatch) {
      options.per_match = true;
    }
    const finalOptions: HighlightOptions | undefined =
      Object.keys(options).length > 0 ? options : undefined;

    const { cancel } = createHighlight(file, (update: ProgressUpdate) => {
      switch (update.phase) {
        case "uploading":
          setState({
            phase: "uploading",
            fileName: file.name,
            percent: update.percent ?? 0,
          });
          break;
        case "scanning":
          setState({ phase: "scanning", fileName: file.name, analyzerDetail: update.analyzerDetail });
          break;
        case "analyzing":
          setState({ phase: "analyzing", fileName: file.name, analyzerDetail: update.analyzerDetail, matchDetail: update.matchDetail });
          break;
        case "clipping":
          setState({ phase: "clipping", fileName: file.name, matchDetail: update.matchDetail });
          break;
        case "done":
          cancelRef.current = null;
          setState({ phase: "done", downloadUrl: update.downloadUrl ?? "", analysisUrl: update.analysisUrl });
          break;
        case "error":
          cancelRef.current = null;
          setState({
            phase: "error",
            message: update.message ?? "予期しないエラーが発生しました",
          });
          break;
      }
    }, finalOptions);

    cancelRef.current = cancel;
  }, [selectedFile, spectatorMode, perMatch]);

  const handleReset = useCallback(() => {
    if (cancelRef.current) {
      cancelRef.current();
      cancelRef.current = null;
    }
    clearPendingJob();
    setSelectedFile(null);
    setState({ phase: "idle" });
  }, []);

  const getMatchDetail = (): MatchDetail | undefined => {
    if (state.phase === "analyzing") return state.matchDetail;
    if (state.phase === "clipping") return state.matchDetail;
    return undefined;
  };

  return (
    <div className="min-h-screen bg-gray-100">
      <Header />
      <main className="max-w-2xl mx-auto px-4 py-8">
        {state.phase === "idle" && (
          <>
            <DropZone
              onFileSelected={handleFileDrop}
              disabled={false}
              selectedFileName={selectedFile?.name}
            />
            <OptionsPanel
              spectatorMode={spectatorMode}
              onSpectatorModeChange={setSpectatorMode}
              perMatch={perMatch}
              onPerMatchChange={setPerMatch}
              disabled={false}
            />
            {selectedFile && (
              <div className="mt-6 text-center">
                <button
                  onClick={handleStart}
                  className="px-8 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium text-lg"
                >
                  ハイライト作成開始
                </button>
              </div>
            )}
          </>
        )}
        {(state.phase === "uploading" ||
          state.phase === "scanning" ||
          state.phase === "analyzing" ||
          state.phase === "clipping") && (
          <Processing
            phase={state.phase}
            fileName={state.fileName}
            percent={state.phase === "uploading" ? state.percent : undefined}
            analyzerDetail={
              state.phase === "scanning" ? state.analyzerDetail :
              state.phase === "analyzing" ? state.analyzerDetail :
              undefined
            }
            matchDetail={getMatchDetail()}
          />
        )}
        {state.phase === "done" && (
          <ResultView
            downloadUrl={state.downloadUrl}
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
