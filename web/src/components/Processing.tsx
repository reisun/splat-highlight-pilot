interface ProcessingProps {
  phase: "uploading" | "analyzing" | "clipping";
  fileName: string;
  percent?: number;
}

export default function Processing({ phase, fileName, percent }: ProcessingProps) {
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
            Uploading video... {percent ?? 0}%
          </p>
        </>
      ) : (
        <>
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-gray-300 border-t-blue-600 mb-6" />
          <p className="text-lg font-medium text-gray-700">
            {phase === "analyzing"
              ? "Detecting highlights..."
              : "Creating highlight clip..."}
          </p>
        </>
      )}
      <p className="text-sm text-gray-500 mt-1">{fileName}</p>
    </div>
  );
}
