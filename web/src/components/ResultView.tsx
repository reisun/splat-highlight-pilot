interface ResultViewProps {
  downloadUrl: string;
  downloadExpiresAt?: string;
  onReset: () => void;
}

export default function ResultView({ downloadUrl, downloadExpiresAt, onReset }: ResultViewProps) {
  const expires = downloadExpiresAt ? new Date(downloadExpiresAt) : null;
  let expiryLabel = "有効期限を確認できません";
  if (expires && !Number.isNaN(expires.getTime())) {
    const parts = new Intl.DateTimeFormat("ja-JP", {
      timeZone: "Asia/Tokyo",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(expires);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    expiryLabel = `${values.month}月${values.day}日 ${values.hour}時${values.minute}分まで有効（日本時間）`;
  }

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
          <span className="block text-xs mt-1">{expiryLabel}</span>
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
