/* ── MediaForge — Alpine.js Application ─────────────────────────────────── */

document.addEventListener('alpine:init', () => {
  Alpine.store('forge', {
    // ── State ────────────────────────────────────────────────────────
    activeTab: 'single',
    url: '',
    batchUrls: '',
    quality: 'best',
    mediaType: 'all',
    compress: false,
    compressionPreset: 'balanced',
    job: null,
    _jobTimer: null,
    loading: false,
    downloading: false,
    error: null,
    result: null,
    batchResults: null,
    playlistResult: null,
    showFormats: false,
    detectedPlatform: null,

    // ── API Base ─────────────────────────────────────────────────────
    api(path) {
      return window.location.origin + path;
    },

    // ── Detect platform as user types ────────────────────────────────
    async detectPlatform() {
      if (!this.url || this.url.length < 10) {
        this.detectedPlatform = null;
        return;
      }
      try {
        const resp = await fetch(this.api(`/detect?url=${encodeURIComponent(this.url)}`));
        const data = await resp.json();
        this.detectedPlatform = data.is_supported ? data.platform : null;
      } catch {
        this.detectedPlatform = null;
      }
    },

    // ── Extract single URL ───────────────────────────────────────────
    async extract() {
      if (!this.url.trim()) return;
      this.loading = true;
      this.error = null;
      this.result = null;
      this.playlistResult = null;
      this.showFormats = false;

      try {
        const resp = await fetch(this.api('/extract'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: this.url.trim(),
            media_type: this.mediaType,
            quality: this.quality,
            include_subtitles: true,
          }),
        });

        const data = await resp.json();

        if (!resp.ok || !data.success) {
          throw new Error(data.detail || data.message || 'Extraction failed');
        }

        this.result = data;
      } catch (e) {
        this.error = e.message || 'Something went wrong';
      } finally {
        this.loading = false;
      }
    },

    // ── Extract batch URLs ───────────────────────────────────────────
    async extractBatch() {
      const urls = this.batchUrls
        .split('\n')
        .map(u => u.trim())
        .filter(u => u.length > 0);

      if (urls.length === 0) return;
      if (urls.length > 20) {
        this.error = 'Maximum 20 URLs per batch';
        return;
      }

      this.loading = true;
      this.error = null;
      this.batchResults = null;
      this.result = null;

      try {
        const resp = await fetch(this.api('/extract/batch'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            urls: urls,
            media_type: this.mediaType,
            quality: this.quality,
          }),
        });

        const data = await resp.json();
        this.batchResults = data;

        if (data.failed > 0 && data.successful === 0) {
          this.error = `All ${data.failed} extractions failed`;
        }
      } catch (e) {
        this.error = e.message || 'Batch extraction failed';
      } finally {
        this.loading = false;
      }
    },

    // ── Extract playlist ─────────────────────────────────────────────
    async extractPlaylist() {
      if (!this.url.trim()) return;
      this.loading = true;
      this.error = null;
      this.playlistResult = null;
      this.result = null;

      try {
        const resp = await fetch(this.api('/playlist'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: this.url.trim(),
            limit: 100,
          }),
        });

        const data = await resp.json();
        if (!resp.ok || !data.success) {
          throw new Error(data.detail || 'Playlist extraction failed');
        }
        this.playlistResult = data;
      } catch (e) {
        this.error = e.message || 'Playlist extraction failed';
      } finally {
        this.loading = false;
      }
    },

    // ── Action dispatcher ────────────────────────────────────────────
    async submit() {
      this.clearJob();
      if (this.activeTab === 'batch') {
        await this.extractBatch();
      } else if (this.activeTab === 'playlist') {
        await this.extractPlaylist();
      } else {
        await this.extract();
      }
    },

    // ── Download job flow (#8 queue, #11 progress, #13 variant choice) ──

    /** Human-readable stage label for the current job. */
    jobStage() {
      if (!this.job) return '';
      const s = this.job.status;
      if (s === 'queued') return 'Queued…';
      if (s === 'downloading') return 'Downloading…';
      if (s === 'encoding') return `Encoding… ${Math.round(this.job.progress || 0)}%`;
      if (s === 'ready') return 'Ready — choose your file';
      if (s === 'error') return 'Failed';
      return s;
    },

    /** Static size-saving estimate shown before a job runs (#12). */
    compressionEstimate(preset) {
      const map = {
        light: '≈25–40% smaller',
        balanced: '≈40–60% smaller',
        aggressive: '≈50–75% smaller',
      };
      return map[preset] || map.balanced;
    },

    /**
     * Entry point for every download control: plain downloads navigate
     * straight to /dl (fast path); compressed downloads go through the
     * async job flow so we can show progress and offer variant choice.
     */
    requestDownload(url, quality, formatId) {
      if (!url) return;
      this.error = null;
      if (!this.compressionActive()) {
        window.location.href = this.dlUrl(url, quality, formatId);
      } else {
        this.startJob(url, quality, formatId);
      }
    },

    async startJob(url, quality, formatId) {
      this.clearJob();
      try {
        const resp = await fetch(this.api('/dl/start'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url,
            quality: quality || this.quality,
            format_id: formatId || null,
            compress: true,
            compression_preset: this.compressionPreset,
          }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Could not start download');
        this.job = { job_id: data.job_id, status: data.status, progress: 0, served: null };
        this._jobTimer = setInterval(() => this.pollJob(), 700);
      } catch (e) {
        this.error = e.message || 'Could not start download';
      }
    },

    async pollJob() {
      if (!this.job) return;
      try {
        const resp = await fetch(this.api(`/dl/status/${this.job.job_id}`));
        if (!resp.ok) throw new Error('Download job expired');
        const s = await resp.json();
        const served = this.job.served;
        this.job = { ...s, served };
        if (s.status === 'ready' || s.status === 'error') {
          clearInterval(this._jobTimer);
          this._jobTimer = null;
        }
      } catch (e) {
        if (this.job) {
          this.job.status = 'error';
          this.job.error = e.message || 'Download job failed';
        }
        clearInterval(this._jobTimer);
        this._jobTimer = null;
      }
    },

    clearJob() {
      if (this._jobTimer) clearInterval(this._jobTimer);
      this._jobTimer = null;
      this.job = null;
    },

    jobFileUrl(variant) {
      return this.job ? this.api(`/dl/file/${this.job.job_id}?variant=${variant}`) : '#';
    },

    /** Remember which variant the user picked (the server drops the other). */
    chooseVariant(variant) {
      if (this.job) this.job.served = variant;
    },

    // ── Download helpers ─────────────────────────────────────────────

    /** Whether the next download will be FFmpeg-compressed. */
    compressionActive() {
      return this.compress && this.quality !== 'audio_only';
    },

    /**
     * Build download URL that triggers yt-dlp download on the server.
     * The /dl endpoint takes the ORIGINAL source URL (not CDN URL)
     * and uses yt-dlp to handle HLS, auth, format merging, etc.
     */
    dlUrl(sourceUrl, quality, formatId) {
      if (!sourceUrl) return '#';
      let href = this.api(`/dl?url=${encodeURIComponent(sourceUrl)}&quality=${encodeURIComponent(quality || 'best')}`);
      if (formatId) {
        href += `&format_id=${encodeURIComponent(formatId)}`;
      }
      // Append FFmpeg compression params when the toggle is on
      // (audio-only output is skipped — the backend passes those through).
      if (this.compressionActive()) {
        href += `&compress=true&compression_preset=${encodeURIComponent(this.compressionPreset)}`;
      }
      return href;
    },

    /**
     * Trigger download via the yt-dlp backend endpoint.
     * Uses window.location to navigate to the /dl endpoint,
     * which returns a FileResponse with Content-Disposition: attachment.
     */
    triggerDownload(sourceUrl, quality, formatId) {
      if (!sourceUrl) return;
      this.downloading = true;
      const href = this.dlUrl(sourceUrl, quality || this.quality, formatId);
      window.location.href = href;
      // Reset downloading state after a delay (file download starts)
      setTimeout(() => { this.downloading = false; }, 5000);
    },

    // ── Helpers ──────────────────────────────────────────────────────
    formatDuration(seconds) {
      if (!seconds) return '';
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = Math.floor(seconds % 60);
      if (h > 0) return `${h}h ${m}m ${s}s`;
      if (m > 0) return `${m}m ${s}s`;
      return `${s}s`;
    },

    formatNumber(n) {
      if (n == null) return '';
      if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
      if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
      return n.toLocaleString();
    },

    formatSize(bytes) {
      if (!bytes) return '';
      if (bytes >= 1_073_741_824) return (bytes / 1_073_741_824).toFixed(1) + ' GB';
      if (bytes >= 1_048_576) return (bytes / 1_048_576).toFixed(0) + ' MB';
      if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB';
      return bytes + ' B';
    },

    formatBitrate(kbps) {
      if (!kbps) return '';
      if (kbps >= 1000) return (kbps / 1000).toFixed(1) + ' Mbps';
      return Math.round(kbps) + ' Kbps';
    },

    formatDate(dateStr) {
      if (!dateStr || dateStr.length < 8) return '';
      return `${dateStr.slice(0, 4)}-${dateStr.slice(4, 6)}-${dateStr.slice(6, 8)}`;
    },

    mediaTypeIcon(type) {
      const icons = { video: '🎬', audio: '🎵', image: '🖼️', all: '📦' };
      return icons[type] || '📦';
    },

    platformEmoji(platform) {
      const map = {
        'YouTube': '▶️',
        'Twitter/X': '🐦',
        'Instagram': '📸',
        'TikTok': '🎵',
        'Facebook': '👤',
        'Vimeo': '🎥',
        'Twitch': '🎮',
        'SoundCloud': '☁️',
        'Reddit': '🔗',
        'Pornhub': '🔞',
        'XVideos': '🔞',
        'xHamster': '🔞',
      };
      return map[platform] || '🌐';
    },

    formatType(f) {
      const hasV = f.has_video && f.vcodec !== 'none' && f.vcodec !== '';
      const hasA = f.has_audio && f.acodec !== 'none' && f.acodec !== '';
      if (hasV && hasA) return 'muxed';
      if (hasV) return 'video';
      return 'audio';
    },

    formatTypeLabel(f) {
      const t = this.formatType(f);
      return t === 'muxed' ? 'A+V' : t === 'video' ? 'V' : 'A';
    },

    copyUrl(url) {
      navigator.clipboard.writeText(url).catch(() => {});
    },
  });
});
