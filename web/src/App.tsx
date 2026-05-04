import { useState, useCallback, useRef } from "react";
import Header from "./components/Header";
import DropZone from "./components/DropZone";
import Processing from "./components/Processing";
import ResultView from "./components/ResultView";
import ErrorMessage from "./components/ErrorMessage";
import { createHighlight, type ApiError } from "./api";

type AppState =
  | { phase: "idle" }
  | { phase: "processing"; fileName: string }
  | { phase: "done"; videoUrl: string }
  | { phase: "error"; message: string };

export default function App() {
  const [state, setState] = useState<AppState>({ phase: "idle" });
  const videoUrlRef = useRef<string | null>(null);

  const cleanup = useCallback(() => {
    if (videoUrlRef.current) {
      URL.revokeObjectURL(videoUrlRef.current);
      videoUrlRef.current = null;
    }
  }, []);

  const handleFileSelected = useCallback(
    async (file: File) => {
      cleanup();
      setState({ phase: "processing", fileName: file.name });

      try {
        const result = await createHighlight(file);
        videoUrlRef.current = result.videoUrl;
        setState({ phase: "done", videoUrl: result.videoUrl });
      } catch (err: unknown) {
        const apiError = err as ApiError;
        const message =
          apiError.message ||
          (err instanceof Error ? err.message : "An unexpected error occurred.");
        setState({ phase: "error", message });
      }
    },
    [cleanup],
  );

  const handleReset = useCallback(() => {
    cleanup();
    setState({ phase: "idle" });
  }, [cleanup]);

  return (
    <div className="min-h-screen bg-gray-100">
      <Header />
      <main className="max-w-2xl mx-auto px-4 py-8">
        {state.phase === "idle" && (
          <DropZone
            onFileSelected={handleFileSelected}
            disabled={false}
          />
        )}
        {state.phase === "processing" && (
          <Processing fileName={state.fileName} />
        )}
        {state.phase === "done" && (
          <ResultView videoUrl={state.videoUrl} onReset={handleReset} />
        )}
        {state.phase === "error" && (
          <ErrorMessage message={state.message} onRetry={handleReset} />
        )}
      </main>
    </div>
  );
}
