/* ----------------------------------------------------------------------
   DeltaTrack — server-side compare flow

   Pick a start PDF and an end PDF, POST them to /api/compare?output=html,
   and open the standalone HTML report in a new browser tab. Nothing is
   uploaded until the user clicks Compare; nothing is stored after the
   response comes back.
   ---------------------------------------------------------------------- */
(function () {
  const MAX_BYTES = 150 * 1024 * 1024; // keep in sync with server MAX_UPLOAD_BYTES
  const PDF_SIG = '%PDF';

  const $ = (id) => document.getElementById(id);

  const files = { start: null, end: null };

  // Selected upload format ('pdf' | 'xml'); drives validation + the API param.
  const selectedFormat = () =>
    (document.querySelector('input[name="format"]:checked') || {}).value || 'pdf';

  // --- Slot wiring (browse + drag/drop) ------------------------------------

  function wireSlot(which) {
    const slot = $(`${which}-slot`);
    const input = $(`${which}-input`);
    const nameEl = $(`${which}-name`);

    const accept = (file) => {
      files[which] = file || null;
      nameEl.textContent = file ? file.name : '';
      slot.classList.toggle('has-file', !!file);
      clearMessages();
      updateButton();
    };

    slot.addEventListener('click', () => input.click());
    slot.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
    });
    input.addEventListener('change', () => accept(input.files[0]));

    ['dragenter', 'dragover'].forEach((ev) =>
      slot.addEventListener(ev, (e) => { e.preventDefault(); slot.classList.add('is-dragover'); })
    );
    ['dragleave', 'drop'].forEach((ev) =>
      slot.addEventListener(ev, (e) => { e.preventDefault(); slot.classList.remove('is-dragover'); })
    );
    slot.addEventListener('drop', (e) => {
      const file = e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) accept(file);
    });
  }

  function updateButton() {
    $('compare-btn').disabled = !(files.start && files.end);
  }

  // --- Client-side pre-checks (server re-validates regardless) --------------

  async function validate(file, label, fmt) {
    if (file.size === 0) return `${label} is empty.`;
    if (file.size > MAX_BYTES) return `${label} is larger than 150 MB.`;
    if (fmt === 'xml') {
      const head = (await file.slice(0, 64).text()).replace(/^﻿/, '').trimStart();
      if (head[0] !== '<') return `${label} doesn't look like XML.`;
    } else {
      const head = await file.slice(0, 4).text();
      if (head !== PDF_SIG) return `${label} doesn't look like a PDF.`;
    }
    return null;
  }

  // --- Submit --------------------------------------------------------------

  // Shown in the new tab while the server renders. A large bill takes tens of
  // seconds, and an about:blank tab is indistinguishable from a stalled one, so
  // the placeholder names the work and animates to show it is still running.
  //
  // Its CSS travels inline rather than as a link to styles.css, so it paints the
  // moment it is written, with no request to wait on while the server is busy.
  // Its colours and font are this page's design tokens, copied when Compare is
  // clicked (#773): the rules below name tokens, and pendingHtml() declares each
  // one with the value this page currently computes for it. styles.css generates
  // those tokens from src/deltatrack/styles/tokens.css, and the generator reads
  // this file too, so every token named here is declared on this page.
  const PENDING_CSS = `
  html,body{height:100%;margin:0}
  body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;
    background:var(--background);color:var(--foreground);font-family:var(--font-sans)}
  .spinner{width:44px;height:44px;border:4px solid var(--border);border-top-color:var(--primary);
    border-radius:50%;animation:spin 900ms linear infinite}
  h1{font-size:1.15rem;font-weight:600;margin:0}
  p{margin:0;font-size:.85rem;color:var(--muted-foreground)}
  @keyframes spin{to{transform:rotate(360deg)}}
  @media (prefers-reduced-motion:reduce){.spinner{animation-duration:2.4s}}
`;
  const PENDING_TOKENS = [...new Set(Array.from(PENDING_CSS.matchAll(/var\((--[\w-]+)\)/g), (m) => m[1]))];

  // Read at the click, not at load, so the tab matches the page as it is now. A
  // token this page cannot compute (its stylesheet failed to load) is declared
  // empty, and the rule using it falls back to the browser default: plain but
  // readable, never an error on the way to opening the tab.
  function pendingHtml() {
    const computed = getComputedStyle(document.documentElement);
    const tokens = PENDING_TOKENS.map((name) => `${name}:${computed.getPropertyValue(name).trim()};`).join('');
    return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Diff in progress — DeltaTrack</title>
<style>:root{${tokens}}${PENDING_CSS}</style></head>
<body>
  <div class="spinner" role="status" aria-live="polite" aria-label="Diff in progress"></div>
  <h1>Diff in progress…</h1>
  <p>Large bills can take a minute. This tab will fill in when the report is ready.</p>
</body></html>`;
  }

  // Open the tab synchronously on user click so the browser treats it as
  // allowed. Do NOT pass "noopener" here — that makes window.open return null
  // even when the tab opens, which breaks document.write below.
  function openReportTab() {
    const tab = window.open('about:blank', '_blank');
    if (tab) writeTab(tab, pendingHtml());
    return tab;
  }

  function writeTab(tab, html) {
    tab.document.open();
    tab.document.write(html);
    tab.document.close();
  }

  // Final write: sever the opener link only once the report is in place, so the
  // placeholder write above can't be affected by dropping the reference.
  function writeReportTab(tab, html) {
    writeTab(tab, html);
    tab.opener = null;
  }

  async function onCompare() {
    clearMessages();
    const fmt = selectedFormat();
    const kind = fmt.toUpperCase();
    const errs = [
      await validate(files.start, `Start ${kind}`, fmt),
      await validate(files.end, `End ${kind}`, fmt),
    ].filter(Boolean);
    if (errs.length) { showError(errs.join(' ')); return; }

    const tab = openReportTab();
    if (!tab) {
      showError('Pop-up blocked. Allow pop-ups for this site to view the report.');
      return;
    }

    setLoading(true);
    const body = new FormData();
    body.append('start_file', files.start);
    body.append('end_file', files.end);

    try {
      const res = await fetch(`/api/compare?output=html&format=${fmt}`, { method: 'POST', body });
      if (!res.ok) {
        let detail = `Request failed (HTTP ${res.status}).`;
        try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
        throw new Error(detail);
      }
      const html = await res.text();
      writeReportTab(tab, html);
      showSuccess('Report opened in a new tab. You can compare another pair here.');
    } catch (err) {
      if (tab) tab.close();
      showError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }

  function setLoading(on) {
    $('compare-btn').disabled = on || !(files.start && files.end);
    $('compare-btn').textContent = on ? 'Comparing…' : 'Compare';
  }

  function showError(msg) {
    const el = $('upload-error');
    el.textContent = msg;
    el.hidden = false;
    $('upload-success').hidden = true;
  }

  function showSuccess(msg) {
    const el = $('upload-success');
    el.textContent = msg;
    el.hidden = false;
    $('upload-error').hidden = true;
  }

  function clearMessages() {
    $('upload-error').hidden = true;
    $('upload-success').hidden = true;
  }

  // --- Format toggle -------------------------------------------------------
  // Switching type clears any chosen files (a PDF is invalid under XML and vice
  // versa) and re-points the native file picker's accept filter + the note.

  function applyFormat() {
    const fmt = selectedFormat();
    const accept = fmt === 'xml' ? 'application/xml,text/xml,.xml' : 'application/pdf,.pdf';
    ['start', 'end'].forEach((which) => {
      const input = $(`${which}-input`);
      input.value = '';
      input.setAttribute('accept', accept);
      files[which] = null;
      $(`${which}-name`).textContent = '';
      $(`${which}-slot`).classList.remove('has-file');
    });
    $('upload-note').textContent = `${fmt.toUpperCase()} · up to 150 MB each · report opens in a new tab`;
    clearMessages();
    updateButton();
  }

  // --- Init ----------------------------------------------------------------

  wireSlot('start');
  wireSlot('end');
  document
    .querySelectorAll('input[name="format"]')
    .forEach((el) => el.addEventListener('change', applyFormat));
  applyFormat();
  updateButton();
  $('compare-btn').addEventListener('click', onCompare);
})();
