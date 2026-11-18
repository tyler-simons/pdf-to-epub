(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    upload: $('upload-panel'), drop: $('dropzone'), file: $('file-input'), title: $('title-input'),
    configNote: $('config-note'), aiNote: $('ai-note'), selected: $('selected-file'), convert: $('convert-button'), progress: $('progress-panel'), ready: $('ready-panel'), reader: $('reader-panel'),
    challenge: $('challenge-container'), challengeStatus: $('challenge-status'), challengeRetry: $('challenge-retry'),
    progressTitle: $('progress-title'), detail: $('progress-detail'), fill: $('progress-fill'), status: $('status-line'), error: $('error-box'), readyTitle: $('ready-title'), expiry: $('expiry-line'), report: $('report'), warning: $('warning-note'),
  };
  const STORAGE_KEY = 'leaf-active-job';
  const state = { file: null, config: null, configReady: false, challengeToken: null, challengeWidget: null, challengeScript: null, directScript: null, job: null, pollTimer: null, request: null, generation: 0, fontSize: 18 };

  function show(panel) {
    [els.upload, els.progress, els.ready, els.reader].forEach((item) => item.classList.add('hidden'));
    panel.classList.remove('hidden');
  }
  function setBusy(on) {
    const needsChallenge = Boolean(state.config?.turnstile_site_key);
    els.convert.disabled = on || !state.file || !state.configReady || (needsChallenge && !state.challengeToken);
    els.convert.textContent = on ? 'Uploading PDF…' : 'Convert PDF to EPUB';
  }
  function bytes(value) { return value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / 1024 / 1024).toFixed(1)} MB`; }
  function selectFile(file) {
    if (!file) return;
    if (!(file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf'))) { showError('Please choose a PDF file.'); return; }
    if (state.config?.max_upload_mb && file.size > state.config.max_upload_mb * 1024 * 1024) { showError(`This file is larger than the ${state.config.max_upload_mb} MB limit.`); return; }
    state.file = file;
    els.selected.textContent = `${file.name} · ${bytes(file.size)}`;
    els.selected.classList.remove('hidden');
    els.error.classList.add('hidden');
    setBusy(false);
  }
  function showError(message) {
    els.error.textContent = message;
    els.error.classList.remove('hidden');
    show(els.progress);
    els.progressTitle.textContent = 'Something went wrong';
    els.detail.textContent = 'Your original PDF is still on your device.';
    els.fill.style.width = '0%';
    els.status.textContent = '';
  }
  async function api(url, options = {}, token = state.job?.token, signal) {
    const headers = new Headers(options.headers || {});
    if (token) headers.set('Authorization', `Bearer ${token}`);
    const response = await fetch(url, { ...options, headers, signal });
    if (!response.ok) {
      let message = `Request failed (${response.status}).`;
      try {
        const data = await response.json();
        if (data.detail) message = typeof data.detail === 'string' ? data.detail : (data.detail.message || message);
        else if (data.error) message = typeof data.error === 'string' ? data.error : (data.error.message || message);
      } catch (_) {}
      const error = new Error(message); error.status = response.status; throw error;
    }
    return response;
  }
  async function loadConfig() {
    try {
      const response = await fetch('/api/config');
      if (!response.ok) throw new Error('Configuration could not be loaded.');
      state.config = await response.json();
      state.configReady = true;
      els.aiNote.classList.toggle('hidden', state.config.ai_enabled !== true);
      if (state.config.max_upload_mb) els.configNote.textContent = `Up to ${state.config.max_upload_mb} MB · Private storage · Files deleted after 24 hours.`;
      else els.configNote.textContent = 'Private storage · Files deleted after 24 hours.';
      if (state.config.turnstile_site_key) await setupChallenge(state.config.turnstile_site_key);
      else setBusy(false);
    } catch (_) {
      state.configReady = false;
      els.challengeStatus.textContent = 'Could not load the upload settings. Reload the page to try again.';
      setBusy(false);
    }
  }
  function loadTurnstileScript() {
    if (window.turnstile) return Promise.resolve();
    if (state.challengeScript) return state.challengeScript;
    state.challengeScript = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
      script.async = true; script.defer = true;
      script.onload = () => window.turnstile ? resolve() : reject(new Error('Verification could not be loaded.'));
      script.onerror = () => reject(new Error('Verification could not be loaded.'));
      document.head.append(script);
    });
    return state.challengeScript;
  }
  async function setupChallenge(sitekey) {
    els.challenge.classList.remove('hidden');
    els.challengeStatus.textContent = 'Loading browser verification…';
    try {
      await loadTurnstileScript();
      if (state.challengeWidget !== null) window.turnstile.remove(state.challengeWidget);
      state.challengeToken = null;
      state.challengeWidget = window.turnstile.render(els.challenge, {
        sitekey,
        action: 'convert',
        callback(token) {
          state.challengeToken = token;
          els.challengeStatus.textContent = '';
          els.challengeRetry.classList.add('hidden');
          setBusy(false);
        },
        'expired-callback'() {
          state.challengeToken = null;
          els.challengeStatus.textContent = 'Verification expired. Complete it again to continue.';
          setBusy(false);
        },
        'error-callback'() {
          state.challengeToken = null;
          els.challengeStatus.textContent = 'Couldn’t verify this browser. Try again.';
          els.challengeRetry.classList.remove('hidden');
          setBusy(false);
        },
      });
      els.challengeStatus.textContent = 'Complete the browser verification to convert your PDF.';
    } catch (_) {
      state.challengeToken = null;
      state.challengeScript = null;
      els.challengeStatus.textContent = 'Could not load browser verification. Check your connection and try again.';
      els.challengeRetry.classList.remove('hidden');
      setBusy(false);
    }
  }
  function resetChallenge(message = '') {
    state.challengeToken = null;
    if (state.challengeWidget !== null && window.turnstile) {
      try { window.turnstile.reset(state.challengeWidget); } catch (_) {}
    }
    els.challengeStatus.textContent = message || (state.config?.turnstile_site_key ? 'Complete the browser verification to continue.' : '');
    setBusy(false);
  }
  function loadDirectUploader() {
    if (typeof window.leafBlobUpload === 'function') return Promise.resolve(window.leafBlobUpload);
    if (state.directScript) return state.directScript;
    state.directScript = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/blob-upload.js'; script.async = true;
      script.onload = () => typeof window.leafBlobUpload === 'function' ? resolve(window.leafBlobUpload) : reject(new Error('Direct upload support could not be loaded.'));
      script.onerror = () => reject(new Error('Direct upload support could not be loaded.'));
      document.head.append(script);
    }).catch((error) => { state.directScript = null; throw error; });
    return state.directScript;
  }
  function saveJob() { if (state.job) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state.job)); }
  async function deletePendingJob(job) {
    if (!job?.id || !job?.token) return;
    try { await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: 'DELETE' }, job.token); } catch (_) { /* Best effort cleanup; reset still returns to upload. */ }
  }
  async function submitDirect(generation, controller) {
    const headers = { 'Content-Type': 'application/json' };
    if (state.config?.turnstile_site_key) headers['X-Challenge-Token'] = state.challengeToken;
    const response = await api('/api/jobs', {
      method: 'POST',
      headers,
      body: JSON.stringify({ filename: state.file.name, size: state.file.size, title: els.title.value.trim() || null }),
    }, null, controller.signal);
    const job = await response.json();
    if (generation !== state.generation) return;
    state.job = { id: job.id, token: job.token, phase: 'uploading', title: els.title.value.trim() || null };
    saveJob();

    els.progressTitle.textContent = 'Uploading PDF';
    els.detail.textContent = 'Sending your file to private storage.';
    els.fill.style.width = '18%';
    els.status.textContent = 'Uploading original PDF…';
    let uploaded = false;
    try {
      if (!job.input_path) throw new Error('The upload could not be prepared. Start over and try again.');
      const upload = await loadDirectUploader();
      if (generation !== state.generation) return;
      const blob = await upload(job.input_path, state.file, {
        access: 'private',
        handleUploadUrl: '/api/blob-upload',
        headers: { Authorization: `Bearer ${job.token}` },
        clientPayload: JSON.stringify({ id: job.id }),
        multipart: false,
        abortSignal: controller.signal,
        onUploadProgress(event) {
          const percentage = Math.max(0, Math.min(100, Number(event.percentage) || 0));
          els.fill.style.width = `${18 + percentage * 0.7}%`;
          els.status.textContent = `Uploading original PDF… ${Math.round(percentage)}%`;
        },
      });
      uploaded = true;
      if (generation !== state.generation) return;
      state.job.phase = 'finalizing';
      saveJob();
      els.progressTitle.textContent = 'Starting conversion';
      els.detail.textContent = 'Your PDF is uploaded. Preparing the conversion.';
      els.status.textContent = 'Finalizing upload…';
      const finalizeResponse = await api(`/api/jobs/${encodeURIComponent(job.id)}/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pathname: blob.pathname }),
      }, job.token, controller.signal);
      const finalized = await finalizeResponse.json();
      if (generation !== state.generation) return;
      state.job.phase = 'active';
      state.job.title = finalized.title || state.job.title;
      saveJob();
      state.request = null;
      resetChallenge();
      setBusy(false);
      startPolling();
    } catch (error) {
      if (generation !== state.generation || error.name === 'AbortError') return;
      if (!uploaded && state.job?.id === job.id) {
        await deletePendingJob(state.job);
        if (generation !== state.generation) return;
        state.job = null;
        sessionStorage.removeItem(STORAGE_KEY);
      }
      state.request = null;
      resetChallenge();
      setBusy(false);
      const message = uploaded
        ? `The PDF uploaded, but conversion could not be started. Start over and select the file again. ${error.message || ''}`.trim()
        : `The PDF upload did not complete. The temporary upload was removed when possible. Start over and select the file again. ${error.message || ''}`.trim();
      showError(message);
    }
  }
  async function submit() {
    if (!state.file || state.request) return;
    const generation = ++state.generation;
    const controller = new AbortController(); state.request = controller;
    setBusy(true); els.error.classList.add('hidden'); show(els.progress);
    els.progressTitle.textContent = 'Sending your PDF'; els.detail.textContent = 'This may take a moment.'; els.fill.style.width = '12%'; els.status.textContent = 'Uploading securely…';
    try {
      if (state.config?.upload_mode === 'direct') {
        await submitDirect(generation, controller);
        return;
      }
      const body = new FormData(); body.append('file', state.file);
      if (els.title.value.trim()) body.append('title', els.title.value.trim());
      const headers = {};
      if (state.config?.turnstile_site_key) {
        if (!state.challengeToken) throw new Error('Complete the browser verification to continue.');
        headers['X-Challenge-Token'] = state.challengeToken;
      }
      const response = await api('/api/jobs', { method: 'POST', body, headers }, null, controller.signal);
      const job = await response.json();
      if (generation !== state.generation) return;
      state.job = { id: job.id, token: job.token };
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state.job));
      state.request = null; resetChallenge(); setBusy(false); startPolling();
    } catch (error) {
      if (generation !== state.generation || error.name === 'AbortError') return;
      state.request = null; resetChallenge(); setBusy(false); showError(error.message || 'We couldn’t upload this PDF. Please try again.');
    }
  }
  function stageCopy(job) {
    const stages = { queued: ['Queued', 'Conversion will begin shortly.'], processing: ['Converting PDF', 'Preparing the EPUB.'], ready: ['Conversion complete', 'Your EPUB is ready to download.'], failed: ['Conversion failed', 'We couldn’t finish this PDF.'], expired: ['This book has expired', 'For privacy, files are removed after 24 hours.'] };
    const words = stages[job.status] || ['Preparing conversion', 'Your PDF is being processed.'];
    els.progressTitle.textContent = words[0]; els.detail.textContent = words[1];
    els.fill.style.width = job.status === 'queued' ? '24%' : job.status === 'processing' ? '62%' : job.status === 'ready' ? '100%' : '0%';
    els.status.textContent = job.stage || (job.status === 'queued' ? 'Waiting to begin…' : job.status === 'processing' ? 'Working on your book…' : '');
  }
  function stopPolling() { if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer = null; }
  async function poll() {
    if (!state.job) return;
    const active = state.job;
    try {
      const response = await api(`/api/jobs/${encodeURIComponent(active.id)}`);
      const job = await response.json();
      if (state.job !== active) return;
      stageCopy(job);
      if (job.status === 'ready') { stopPolling(); presentReady(job); return; }
      if (job.status === 'failed' || job.status === 'expired') { stopPolling(); els.error.textContent = job.error || (job.status === 'expired' ? 'This file has expired and was deleted.' : 'This PDF could not be converted.'); els.error.classList.remove('hidden'); return; }
      state.pollTimer = setTimeout(poll, 1800);
    } catch (error) {
      if (state.job !== active) return;
      if (error.name === 'AbortError') return;
      if ([401, 403, 404, 410].includes(error.status)) {
        stopPolling();
        els.error.textContent = error.status === 410 ? 'This book has expired and was deleted. Start over to choose another PDF.' : `${error.message} Start over to choose another PDF.`;
        els.error.classList.remove('hidden');
        els.progressTitle.textContent = error.status === 410 ? 'This book has expired' : 'This book is unavailable';
        els.detail.textContent = 'You can return to the upload form and choose another PDF.';
        els.status.textContent = '';
        return;
      }
      els.status.textContent = error.message || 'Connection interrupted. Retrying…';
      state.pollTimer = setTimeout(poll, 3500);
    }
  }
  function startPolling() { stopPolling(); show(els.progress); poll(); }
  function formatDate(value) {
    if (!value) return '';
    const date = new Date(typeof value === 'number' ? value * 1000 : value); if (Number.isNaN(date.getTime())) return '';
    return `Available until ${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)}.`;
  }
  function presentWarnings(target, warnings) {
    target.replaceChildren();
    const groups = new Map();
    warnings.filter((item) => typeof item === 'string').forEach((warning) => {
      const match = warning.match(/^Page (\d+):\s*(.*)$/);
      const message = match ? match[2] : warning;
      if (!groups.has(message)) groups.set(message, []);
      if (match) groups.get(message).push(Number(match[1]));
    });
    target.classList.toggle('hidden', groups.size === 0);
    if (!groups.size) return;
    const summary = document.createElement('p');
    summary.textContent = 'Your EPUB is ready. Some text or layouts may need a quick check.';
    const details = document.createElement('details');
    const label = document.createElement('summary'); label.textContent = 'View conversion notes';
    const list = document.createElement('ul');
    groups.forEach((pages, message) => {
      const item = document.createElement('li');
      const description = message.includes('possible broken ligatures')
        ? 'Some joined letters, such as fi or fl, may not have copied correctly from the PDF. Check these pages in the preview.'
        : message;
      const unique = [...new Set(pages)].sort((a, b) => a - b);
      item.textContent = description + (unique.length ? ` (${unique.length === 1 ? 'Page' : 'Pages'} ${unique.join(', ')})` : '');
      list.append(item);
    });
    details.append(label, list); target.append(summary, details);
  }
  function presentReady(job) {
    show(els.ready); els.readyTitle.textContent = job.title || els.title.value.trim() || 'A new way to read.';
    els.expiry.textContent = formatDate(job.expires_at) || 'Your EPUB is ready to download.';
    const report = job.report;
    els.report.replaceChildren();
    if (report && typeof report === 'object') {
      const values = [];
      const pages = Number.isFinite(report.converted_pages) ? report.converted_pages : report.pages;
      const images = Number.isFinite(report.image_count) ? report.image_count : report.images;
      if (Number.isFinite(pages)) values.push([pages, pages === 1 ? 'page' : 'pages']);
      if (Number.isFinite(report.ocr_pages) && report.ocr_pages > 0) values.push([report.ocr_pages, 'pages read from scans']);
      if (Number.isFinite(images) && images > 0) values.push([images, images === 1 ? 'image' : 'images']);
      values.forEach(([number, label]) => { const item = document.createElement('span'); const strong = document.createElement('strong'); strong.textContent = number; item.append(strong, ` ${label}`); els.report.append(item); });
      if (values.length) els.report.classList.remove('hidden'); else els.report.classList.add('hidden');
      const warnings = Array.isArray(report.warnings) ? report.warnings.filter((item) => typeof item === 'string') : [];
      presentWarnings(els.warning, warnings);
    } else { els.report.classList.add('hidden'); els.warning.classList.add('hidden'); }
  }
  async function download() {
    if (!state.job) return;
    const button = $('download-button'); button.disabled = true; button.textContent = 'Preparing download…';
    try {
      const response = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/download`);
      const blob = await response.blob(); const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = (els.readyTitle.textContent || 'Book').replace(/[\\/:*?"<>|]+/g, '').trim() + '.epub';
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { els.warning.textContent = error.message || 'Your download could not be prepared. Please try again.'; els.warning.classList.remove('hidden'); }
    finally { button.disabled = false; button.innerHTML = 'Download EPUB <span aria-hidden="true">↓</span>'; }
  }
  async function deleteBook() {
    if (!state.job) return;
    const button = $('delete-book-button'); button.disabled = true; button.textContent = 'Deleting…';
    try {
      await api(`/api/jobs/${encodeURIComponent(state.job.id)}`, { method: 'DELETE' });
      reset();
    } catch (error) {
      button.disabled = false; button.textContent = 'Delete this book';
      els.warning.textContent = error.status === 409 ? 'This book is still being converted. Wait until it is ready, then try again.' : (error.message || 'This book could not be deleted.');
      els.warning.classList.remove('hidden');
    }
  }
  async function preview() {
    if (!state.job) return;
    const button = $('preview-button'); button.disabled = true; button.textContent = 'Opening preview…';
    try {
      const response = await api(`/api/jobs/${encodeURIComponent(state.job.id)}/preview`); const data = await response.json();
      const title = $('reader-title'); const body = $('reader-body'); title.textContent = data.title || els.readyTitle.textContent;
      body.replaceChildren();
      if (Array.isArray(data.paragraphs)) data.paragraphs.forEach((value) => { if (typeof value !== 'string') return; const paragraph = document.createElement('p'); paragraph.textContent = value; body.append(paragraph); });
      if (Array.isArray(data.warnings) && data.warnings.some((item) => typeof item === 'string')) { const note = document.createElement('div'); note.className = 'warning-note'; presentWarnings(note, data.warnings); body.prepend(note); }
      $('reader-panel').classList.remove('night'); body.style.fontSize = `${state.fontSize}px`; show(els.reader);
    } catch (error) { els.warning.textContent = error.message || 'Preview could not be loaded.'; els.warning.classList.remove('hidden'); }
    finally { button.disabled = false; button.innerHTML = 'Preview your book'; }
  }
  function reset() {
    const pendingJob = state.job?.phase === 'uploading' || state.job?.phase === 'finalizing' ? state.job : null;
    state.generation++; if (state.request) state.request.abort(); state.request = null; stopPolling(); state.job = null; state.file = null;
    if (pendingJob) void deletePendingJob(pendingJob);
    sessionStorage.removeItem(STORAGE_KEY); els.file.value = ''; els.title.value = ''; els.selected.classList.add('hidden'); els.error.classList.add('hidden'); els.warning.classList.add('hidden');
    resetChallenge();
    els.convert.disabled = true; els.convert.textContent = 'Convert PDF to EPUB'; show(els.upload);
  }

  els.drop.addEventListener('click', (event) => { if (event.target !== els.file) els.file.click(); });
  els.drop.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); els.file.click(); } });
  els.file.addEventListener('change', () => selectFile(els.file.files[0]));
  ['dragenter', 'dragover'].forEach((eventName) => els.drop.addEventListener(eventName, (event) => { event.preventDefault(); els.drop.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach((eventName) => els.drop.addEventListener(eventName, (event) => { event.preventDefault(); els.drop.classList.remove('dragover'); }));
  els.drop.addEventListener('drop', (event) => selectFile(event.dataTransfer.files[0]));
  els.convert.addEventListener('click', submit); $('reset-progress').addEventListener('click', reset); $('reset-ready').addEventListener('click', reset);
  $('download-button').addEventListener('click', download); $('preview-button').addEventListener('click', preview);
  const deleteButton = document.createElement('button');
  deleteButton.id = 'delete-book-button'; deleteButton.type = 'button'; deleteButton.className = 'quiet-button'; deleteButton.textContent = 'Delete this book';
  deleteButton.style.display = 'block'; deleteButton.style.margin = '12px auto 0';
  deleteButton.addEventListener('click', deleteBook);
  $('ready-panel').append(deleteButton);
  $('back-to-book').addEventListener('click', () => show(els.ready));
  $('font-smaller').addEventListener('click', () => { state.fontSize = Math.max(14, state.fontSize - 1); $('reader-body').style.fontSize = `${state.fontSize}px`; });
  $('font-larger').addEventListener('click', () => { state.fontSize = Math.min(25, state.fontSize + 1); $('reader-body').style.fontSize = `${state.fontSize}px`; });
  $('theme-toggle').addEventListener('click', () => $('reader-panel').classList.toggle('night'));
  els.challengeRetry.addEventListener('click', () => { if (state.config?.turnstile_site_key) setupChallenge(state.config.turnstile_site_key); });
  loadConfig().then(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null');
      if (saved?.id && saved?.token) {
        state.job = saved; show(els.progress);
        if (saved.phase === 'uploading' || saved.phase === 'finalizing') {
          els.progressTitle.textContent = 'Upload interrupted';
          els.detail.textContent = 'This upload did not finish in this browser session.';
          els.fill.style.width = '0%';
          els.status.textContent = '';
          els.error.textContent = 'Start over to remove the unfinished upload and choose your PDF again.';
          els.error.classList.remove('hidden');
        } else startPolling();
      }
    } catch (_) { sessionStorage.removeItem(STORAGE_KEY); }
  });
})();
