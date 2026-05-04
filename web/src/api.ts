const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

const TIMEOUT_MS = 5 * 60 * 1000;

export interface HighlightResult {
  videoUrl: string;
}

export interface ApiError {
  status: number;
  message: string;
}

export async function createHighlight(
  file: File,
): Promise<HighlightResult> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(`${API_BASE_URL}/highlight`, {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });

    if (!response.ok) {
      let message: string;
      switch (response.status) {
        case 404:
          message = "No highlights were detected in this video.";
          break;
        case 502:
          message = "External service error. Please try again later.";
          break;
        default: {
          const body = await response.text().catch(() => "");
          message = body || `Server error (${response.status})`;
          break;
        }
      }
      const error: ApiError = { status: response.status, message };
      throw error;
    }

    const blob = await response.blob();
    const videoUrl = URL.createObjectURL(blob);
    return { videoUrl };
  } finally {
    clearTimeout(timeoutId);
  }
}
