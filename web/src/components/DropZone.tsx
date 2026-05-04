import { useCallback, useState, useRef } from "react";

interface DropZoneProps {
  onFileSelected: (file: File) => void;
  disabled: boolean;
}

export default function DropZone({ onFileSelected, disabled }: DropZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      if (disabled) return;
      const file = e.dataTransfer.files[0];
      if (file) onFileSelected(file);
    },
    [onFileSelected, disabled],
  );

  const handleDragOver = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      if (!disabled) setIsDragOver(true);
    },
    [disabled],
  );

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handleClick = useCallback(() => {
    if (!disabled) inputRef.current?.click();
  }, [disabled]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) onFileSelected(file);
      if (inputRef.current) inputRef.current.value = "";
    },
    [onFileSelected],
  );

  return (
    <div
      onClick={handleClick}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      className={`
        border-2 border-dashed rounded-lg p-12 text-center cursor-pointer
        transition-colors duration-200
        ${disabled ? "opacity-50 cursor-not-allowed border-gray-300 bg-gray-50" : ""}
        ${isDragOver ? "border-blue-500 bg-blue-50" : "border-gray-400 hover:border-gray-500 hover:bg-gray-50"}
      `}
    >
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        className="hidden"
        onChange={handleChange}
        disabled={disabled}
      />
      <div className="text-gray-600">
        <svg
          className="mx-auto h-12 w-12 mb-4 text-gray-400"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
          />
        </svg>
        <p className="text-lg font-medium">
          Drop a video file here, or click to select
        </p>
        <p className="text-sm text-gray-500 mt-1">
          Supported formats: MP4, MOV, AVI, etc.
        </p>
      </div>
    </div>
  );
}
