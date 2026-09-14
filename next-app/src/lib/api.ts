// API client for the MediaForge FastAPI backend

import type {
  ExtractResponse,
  PlaylistResponse,
  BatchExtractResponse,
  HealthResponse,
  QualityPreset,
  MediaType,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  /** Health check */
  async health(): Promise<HealthResponse> {
    return fetchJSON<HealthResponse>(`${API_BASE}/health`);
  },

  /** Detect platform from URL */
  async detect(
    url: string
  ): Promise<{ platform: string; platform_key: string; is_supported: boolean }> {
    return fetchJSON(
      `${API_BASE}/detect?url=${encodeURIComponent(url)}`
    );
  },

  /** Extract media info (metadata + formats, no download) */
  async extract(
    url: string,
    quality: QualityPreset = "best",
    mediaType: MediaType = "all"
  ): Promise<ExtractResponse> {
    return fetchJSON<ExtractResponse>(`${API_BASE}/extract`, {
      method: "POST",
      body: JSON.stringify({
        url,
        quality,
        media_type: mediaType,
        include_subtitles: true,
      }),
    });
  },

  /** Get download URL (navigates browser to /dl endpoint) */
  dlUrl(
    sourceUrl: string,
    quality: QualityPreset = "best",
    formatId?: string
  ): string {
    let href = `${API_BASE}/dl?url=${encodeURIComponent(sourceUrl)}&quality=${encodeURIComponent(quality)}`;
    if (formatId) {
      href += `&format_id=${encodeURIComponent(formatId)}`;
    }
    return href;
  },

  /** Extract playlist */
  async playlist(
    url: string,
    limit = 100
  ): Promise<PlaylistResponse> {
    return fetchJSON<PlaylistResponse>(`${API_BASE}/playlist`, {
      method: "POST",
      body: JSON.stringify({ url, limit }),
    });
  },

  /** Batch extract multiple URLs */
  async batchExtract(
    urls: string[],
    quality: QualityPreset = "best",
    mediaType: MediaType = "all"
  ): Promise<BatchExtractResponse> {
    return fetchJSON<BatchExtractResponse>(`${API_BASE}/extract/batch`, {
      method: "POST",
      body: JSON.stringify({ urls, quality, media_type: mediaType }),
    });
  },
};
