import type { AnalyzerDetail, MatchDetail } from "../api";

interface ProcessingProps {
  phase: "uploading" | "scanning" | "analyzing" | "clipping";
  fileName: string;
  percent?: number;
  analyzerDetail?: AnalyzerDetail;
  matchDetail?: MatchDetail;
}

export default function Processing({ phase, fileName, percent, analyzerDetail, matchDetail }: ProcessingProps) {
  const formatElapsed = (startedAt: number | null): string => {
    if (!startedAt) return "";
    const elapsed = Math.floor(Date.now() / 1000 - startedAt);
    const min = Math.floor(elapsed / 60);
    const sec = elapsed % 60;
    return `${min}:${sec.toString().padStart(2, "0")}`;
  };

  return (
    <div className="text-center py-12">
      {phase === "uploading" ? (
        <>
          <div className="w-full max-w-md mx-auto bg-gray-200 rounded-full h-4 mb-4">
            <div
              className="bg-blue-600 h-4 rounded-full transition-all duration-300"
              style={{ width: `${percent ?? 0}%` }}
            />
          </div>
          <p className="text-lg font-medium text-gray-700">
            動画をアップロード中... {percent ?? 0}%
          </p>
        </>
      ) : (
        <>
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-gray-300 border-t-blue-600 mb-6" />
          {phase === "scanning" ? (
            <>
              <p className="text-lg font-medium text-gray-700">
                試合をスキャン中...
              </p>
              {analyzerDetail && analyzerDetail.frames_total > 0 && (
                <p className="text-sm text-gray-500 mt-1">
                  フレーム {analyzerDetail.frames_done}/{analyzerDetail.frames_total}
                </p>
              )}
            </>
          ) : phase === "analyzing" && analyzerDetail ? (
            <>
              <p className="text-lg font-medium text-gray-700">
                分析中...
                {matchDetail && matchDetail.total_matches > 0 && (
                  <span className="text-base"> (試合 {matchDetail.current_match}/{matchDetail.total_matches})</span>
                )}
              </p>
              <p className="text-sm text-gray-500 mt-1">
                フレーム {analyzerDetail.frames_done}/{analyzerDetail.frames_total}
                {analyzerDetail.started_at && (
                  <span className="ml-2">(経過 {formatElapsed(analyzerDetail.started_at)})</span>
                )}
              </p>
            </>
          ) : (
            <p className="text-lg font-medium text-gray-700">
              {phase === "analyzing"
                ? "ハイライト検出中..."
                : "ハイライトクリップを作成中..."}
              {matchDetail && matchDetail.total_matches > 0 && (
                <span className="text-base"> (試合 {matchDetail.current_match}/{matchDetail.total_matches})</span>
              )}
            </p>
          )}
        </>
      )}
      <p className="text-sm text-gray-500 mt-1">{fileName}</p>
    </div>
  );
}
