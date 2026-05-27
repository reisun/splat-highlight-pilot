interface ResultViewProps {
  downloadUrl: string;
  onReset: () => void;
}

export default function ResultView({ downloadUrl, onReset }: ResultViewProps) {
  return (
    <div className="space-y-6 text-center">
      <p className="text-lg font-medium text-gray-700">
        ハイライトの作成が完了しました
      </p>
      <div className="flex flex-col gap-3 items-center">
        <a
          href={downloadUrl}
          download
          className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors font-medium w-64"
        >
          ダウンロード
        </a>
        <button
          onClick={onReset}
          className="px-6 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition-colors font-medium w-64"
        >
          別の動画をアップロード
        </button>
      </div>
    </div>
  );
}
