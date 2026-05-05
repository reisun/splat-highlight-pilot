interface ResultViewProps {
  downloadUrl: string;
  onReset: () => void;
}

export default function ResultView({ downloadUrl, onReset }: ResultViewProps) {
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
    </div>
  );
}
