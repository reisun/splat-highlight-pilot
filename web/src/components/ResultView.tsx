import { useState } from "react";
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
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

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
        <div className="mt-6 text-left max-w-lg mx-auto">
          <h3 className="text-sm font-semibold text-gray-600 mb-2">
            Detected Highlights ({highlights.length})
          </h3>
          <ul className="space-y-2">
            {highlights.map((h, i) => (
              <li key={i} className="bg-white rounded-lg shadow-sm border border-gray-200">
                <button
                  type="button"
                  className="w-full p-3 text-left"
                  onClick={() => setExpandedIndex(expandedIndex === i ? null : i)}
                >
                  <div className="flex justify-between items-center">
                    <span className="text-sm font-medium text-gray-700">
                      {formatTime(h.start_seconds)} - {formatTime(h.end_seconds)}
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full">
                        Score {h.peak_intensity}
                      </span>
                      <span className="text-xs text-gray-400">
                        {expandedIndex === i ? "▲" : "▼"}
                      </span>
                    </div>
                  </div>
                  {h.description && (
                    <p className="text-xs text-gray-500 mt-1">{h.description}</p>
                  )}
                </button>

                {expandedIndex === i && h.frames && h.frames.length > 0 && (
                  <div className="px-3 pb-3 border-t border-gray-100">
                    <p className="text-xs font-semibold text-gray-500 mt-2 mb-1">
                      Frame Analysis ({h.frames.length})
                    </p>
                    <div className="space-y-1.5">
                      {h.frames.map((f, fi) => (
                        <div key={fi} className="text-xs bg-gray-50 rounded p-2">
                          <div className="flex justify-between items-center mb-1">
                            <span className="font-medium text-gray-600">
                              {formatTime(f.timestamp_seconds)}
                            </span>
                            <span className="font-medium text-gray-700">
                              Score: {f.score}
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1.5 mb-1">
                            {f.kills_in_log > 0 && (
                              <span className="px-1.5 py-0.5 bg-red-100 text-red-700 rounded">
                                Kill ×{f.kills_in_log}
                              </span>
                            )}
                            {f.assists_in_log > 0 && (
                              <span className="px-1.5 py-0.5 bg-orange-100 text-orange-700 rounded">
                                Assist ×{f.assists_in_log}
                              </span>
                            )}
                            {f.my_special_active && (
                              <span className="px-1.5 py-0.5 bg-purple-100 text-purple-700 rounded">
                                Special
                              </span>
                            )}
                            {f.team_score_increasing && (
                              <span className="px-1.5 py-0.5 bg-green-100 text-green-700 rounded">
                                Score Up
                              </span>
                            )}
                            {f.is_dead && (
                              <span className="px-1.5 py-0.5 bg-gray-300 text-gray-700 rounded">
                                Dead
                              </span>
                            )}
                          </div>
                          {f.description && (
                            <p className="text-gray-500">{f.description}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
