interface OptionsPanelProps {
  spectatorMode: boolean;
  onSpectatorModeChange: (enabled: boolean) => void;
  disabled: boolean;
}

export default function OptionsPanel({
  spectatorMode,
  onSpectatorModeChange,
  disabled,
}: OptionsPanelProps) {
  return (
    <div className="mt-4 p-4 bg-white rounded-lg border border-gray-200">
      <h3 className="text-sm font-medium text-gray-700 mb-3">オプション</h3>
      <label
        className={`flex items-start gap-3 cursor-pointer ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        <input
          type="checkbox"
          checked={spectatorMode}
          onChange={(e) => onSpectatorModeChange(e.target.checked)}
          disabled={disabled}
          className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
        />
        <div>
          <span className="text-sm text-gray-800">
            試合のカウント獲得を考慮しない（観戦用）
          </span>
          <p className="text-xs text-gray-500 mt-0.5">
            ONにすると、キルのみでハイライトを判定します。
            観戦時の動画はONを推奨します。
          </p>
        </div>
      </label>
    </div>
  );
}
