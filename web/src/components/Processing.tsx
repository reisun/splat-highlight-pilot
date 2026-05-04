interface ProcessingProps {
  fileName: string;
}

export default function Processing({ fileName }: ProcessingProps) {
  return (
    <div className="text-center py-12">
      <div className="inline-block animate-spin rounded-full h-12 w-12 border-4 border-gray-300 border-t-blue-600 mb-6" />
      <p className="text-lg font-medium text-gray-700">
        Processing highlight...
      </p>
      <p className="text-sm text-gray-500 mt-1">{fileName}</p>
      <p className="text-sm text-gray-400 mt-4">
        This may take 1-3 minutes. Please wait.
      </p>
    </div>
  );
}
