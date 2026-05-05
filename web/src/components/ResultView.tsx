import type { HighlightSegment } from "../api";

interface ResultViewProps {
  downloadUrl: string;
  highlights?: HighlightSegment[];
  onReset: () => void;
}

function formatTime(seconds: number): string {
  const min = Math.floor(seconds / 60);
  const sec = Math.floor(seconds % 60);
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

export default function ResultView({ downloadUrl, highlights, onReset }: ResultViewProps) {
  return (
    <div className="space-y-6 text-center">
      <p className="text-lg font-medium text-gray-700">
        Your highlight is ready!
      </p>
      <div className="flex gap-4 justify-center">
        <a
          href={downloadUrl}
          download="highlight.mp4"
          className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium"
        >
          Download Highlight
        </a>
        <button
          onClick={onReset}
          className="px-6 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-colors font-medium"
        >
          Upload Another
        </button>
      </div>

      {highlights && highlights.length > 0 && (
        <div className="mt-6 text-left max-w-md mx-auto">
          <h3 className="text-sm font-semibold text-gray-600 mb-2">
            Detected Highlights ({highlights.length})
          </h3>
          <ul className="space-y-2">
            {highlights.map((h, i) => (
              <li key={i} className="bg-white rounded-lg p-3 shadow-sm border border-gray-200">
                <div className="flex justify-between items-center">
                  <span className="text-sm font-medium text-gray-700">
                    {formatTime(h.start_seconds)} - {formatTime(h.end_seconds)}
                  </span>
                  <span className="text-xs px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full">
                    Intensity {h.peak_intensity}/10
                  </span>
                </div>
                {h.description && (
                  <p className="text-xs text-gray-500 mt-1 line-clamp-2">{h.description}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
