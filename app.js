(() => {
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
  const BASE = (window.__APP_BASE__ || "").replace(/\/$/, "");

  function url(path) {
    const p = path.startsWith("/") ? path : `/${path}`;
    return `${BASE}${p}`;
  }

  function mediaUrl(pathOrAbsolute) {
    if (!pathOrAbsolute) return "";
    if (/^https?:\/\//i.test(pathOrAbsolute)) return pathOrAbsolute;
    return url(pathOrAbsolute);
  }

  function toast(msg, isError = false) {
    const el = $("#toast");
    if (!el) return;
    el.hidden = false;
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      el.hidden = true;
    }, 4200);
  }

  async function api(path, opts = {}) {
    const res = await fetch(url(path), {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "request failed");
    return data;
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function urgencyFromText(text, score) {
    const t = (text || "").toLowerCase();
    if (/fire|smoke|blaze|burn|wildfire|flame|charred|urgent|debris|damage/.test(t) || (score || 0) > 0.45) {
      return { level: "high", label: "watch" };
    }
    if (/vehicle|truck|convoy|person|firefighter|traffic|personnel/.test(t) || (score || 0) > 0.28) {
      return { level: "med", label: "monitor" };
    }
    return { level: "low", label: "note" };
  }

  function hitCard(h) {
    const score = typeof h.similarity_score === "number" ? h.similarity_score : null;
    const scoreTxt = score != null ? score.toFixed(3) : "—";
    const pct = score != null ? Math.max(6, Math.min(100, Math.round(score * 100))) : 8;
    const reason = h.reasoning_content || "No Cosmos Reason caption.";
    let timeLabel = h.segment_label || "";
    if (!timeLabel && h.start_sec != null && h.end_sec != null) {
      timeLabel = `${Number(h.start_sec).toFixed(1)}s – ${Number(h.end_sec).toFixed(1)}s`;
    } else if (!timeLabel) {
      timeLabel = "segment";
    }
    const u = urgencyFromText(reason, score);
    return `<article class="hit" data-source="${esc(h.source || "")}" tabindex="0" role="button">
      <div class="hit-top">
        <span class="score">${scoreTxt}</span>
        <span class="urgency ${u.level}">${u.label}</span>
      </div>
      <div class="score-bar" aria-hidden="true"><span style="width:${pct}%"></span></div>
      <p>${esc(reason)}</p>
      <div class="time" style="margin-top:0.45rem">${esc(timeLabel)}</div>
    </article>`;
  }

  function setLoading(btn, on) {
    if (!btn) return;
    btn.disabled = !!on;
    const spin = $(".btn-spin", btn);
    const label = $(".btn-label", btn);
    if (spin) spin.hidden = !on;
    if (label) label.style.opacity = on ? "0.7" : "1";
  }

  async function showDetail(source) {
    const panel = $("#detailPanel");
    if (!source) {
      panel.innerHTML = `<div class="empty-state compact"><p class="empty-title">Clip inspector</p><p class="empty">No segment source on this hit.</p></div>`;
      return;
    }
    panel.innerHTML = `<p class="empty">Loading clip…</p><div class="skeleton-stack"><div class="sk"></div><div class="sk" style="height:10rem"></div></div>`;
    try {
      const [clip, stream] = await Promise.all([
        api(`/api/clip?source=${encodeURIComponent(source)}`),
        api(`/api/stream?source=${encodeURIComponent(source)}`),
      ]);
      const det = clip.detections || {};
      const tags = (det.object_classes || [])
        .map((c) => {
          const n = (det.object_counts || {})[c];
          return `<span class="tag">${esc(c)}${n != null ? ` ×${n}` : ""}</span>`;
        })
        .join("");
      const playUrl = mediaUrl(stream.playback_url || "");
      panel.innerHTML = `
        <h3>Clip detail</h3>
        <video id="detailVideo" controls playsinline preload="metadata" ${playUrl ? `src="${esc(playUrl)}"` : ""}></video>
        <div class="tags">${tags || `<span class="tag">no YOLO classes</span>`}</div>
        <p>${esc(clip.reasoning_content || "")}</p>
        <p class="detail-meta">${esc(source)}</p>
      `;
      const v = $("#detailVideo");
      if (v && playUrl) {
        v.load();
        // Clip inspector: load metadata only; user presses play
      }
    } catch (e) {
      panel.innerHTML = `<div class="empty-state compact"><p class="empty-title">Could not load clip</p><p class="empty">${esc(e.message)}</p></div>`;
      toast(e.message, true);
    }
  }

  function bindHits(container) {
    $$(".hit", container).forEach((el) => {
      const activate = () => {
        $$(".hit").forEach((x) => x.classList.remove("active"));
        el.classList.add("active");
        showDetail(el.dataset.source);
        const detail = $("#detailPanel");
        if (detail && window.matchMedia("(max-width: 959px)").matches) {
          detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      };
      el.addEventListener("click", activate);
      el.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          activate();
        }
      });
    });
  }

  function setActiveChip(q) {
    $$(".chip[data-q]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.q === q);
    });
  }

  async function runSearch(q) {
    const list = $("#resultsList");
    const badge = $("#resultsBadge");
    const btn = $("#searchBtn");
    list.innerHTML = `<div class="skeleton-stack"><div class="sk"></div><div class="sk"></div><div class="sk"></div></div>`;
    badge.hidden = true;
    badge.textContent = "";
    setLoading(btn, true);
    activateTab("results");
    setActiveChip(q);
    try {
      const data = await api("/api/search", {
        method: "POST",
        body: JSON.stringify({ query: q, min_similarity: 0.1 }),
      });
      if (!data.results?.length) {
        list.innerHTML = `<div class="empty-state"><p class="empty-title">No focal hits</p><p class="empty">Nothing on this footage matched “${esc(q)}” (archive raw: ${data.raw_total}). Try “wildfire smoke near the road” or “fire truck at the blaze”.</p><button type="button" class="secondary" id="tryDemo2">Try smoke near road</button></div>`;
        $("#tryDemo2")?.addEventListener("click", () => {
          $("#searchInput").value = "wildfire smoke near the road";
          runSearch("wildfire smoke near the road");
        });
        return;
      }
      badge.hidden = false;
      badge.textContent = String(data.results.length);
      $("#resultsTitle").textContent = `Results for “${q}”`;
      list.innerHTML = data.results.map((h) => hitCard(h)).join("");
      bindHits(list);
      if (data.results[0]?.source) showDetail(data.results[0].source);
    } catch (e) {
      list.innerHTML = `<div class="empty-state"><p class="empty-title">Search failed</p><p class="empty">${esc(e.message)}</p></div>`;
      toast(e.message, true);
    } finally {
      setLoading(btn, false);
    }
  }

  async function loadOps() {
    const grid = $("#opsGrid");
    $$(".ops-hits", grid).forEach((el) => {
      el.innerHTML = `<div class="skeleton-stack"><div class="sk"></div><div class="sk"></div></div>`;
    });
    try {
      const data = await api("/api/ops-board");
      data.columns.forEach((col) => {
        const root = $(`.ops-col[data-col="${col.id}"] .ops-hits`);
        if (!root) return;
        if (!col.hits?.length) {
          root.innerHTML = `<p class="empty">No focal hits in this lane.</p>`;
          return;
        }
        root.innerHTML = `<div class="ops-count">${col.hits.length} moments</div>` + col.hits.map((h) => hitCard(h)).join("");
        bindHits(root);
      });
    } catch (e) {
      $$(".ops-hits", grid).forEach((el) => {
        el.innerHTML = `<p class="empty">${esc(e.message)}</p>`;
      });
      toast(e.message, true);
    }
  }

  function activateTab(name) {
    $$(".tab").forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    $$(".panel").forEach((p) => {
      const on = p.id === `panel-${name}`;
      p.classList.toggle("active", on);
      p.hidden = !on;
    });
  }

  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      activateTab(tab.dataset.tab);
      if (tab.dataset.tab === "ops") loadOps();
    });
  });

  $("#searchForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#searchInput").value.trim();
    if (q) runSearch(q);
  });

  $$(".chip[data-q]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#searchInput").value = btn.dataset.q;
      runSearch(btn.dataset.q);
    });
  });

  $("#tryDemo")?.addEventListener("click", () => {
    $("#searchInput").value = "wildfire smoke near the road";
    runSearch("wildfire smoke near the road");
  });

  $("#loadOps")?.addEventListener("click", loadOps);

  $$(".chip[data-ask]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("#askInput").value = btn.dataset.ask;
      $("#askForm").requestSubmit();
    });
  });

  $("#askForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#askInput").value.trim();
    if (!q) return;
    $("#askAnswer").innerHTML = `<p class="empty">Thinking…</p>`;
    $("#askEvidence").innerHTML = `<div class="skeleton-stack"><div class="sk"></div><div class="sk"></div></div>`;
    try {
      const data = await api("/api/ask", { method: "POST", body: JSON.stringify({ question: q }) });
      $("#askAnswer").innerHTML = `<h3>Answer</h3><div>${esc(data.answer || "No answer")}</div>`;
      const clips = data.evidence_clips || [];
      $("#askEvidence").innerHTML = clips.length
        ? clips.map((h) => hitCard(h)).join("")
        : `<p class="empty">No clip evidence returned.</p>`;
      bindHits($("#askEvidence"));
    } catch (err) {
      $("#askAnswer").innerHTML = `<p class="empty">${esc(err.message)}</p>`;
      $("#askEvidence").innerHTML = "";
      toast(err.message, true);
    }
  });

  $("#briefForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#briefInput").value.trim() || "humanitarian logistics assessment";
    $("#briefOut").innerHTML = `<p class="empty">Drafting brief…</p>`;
    $("#briefEvidence").innerHTML = `<div class="skeleton-stack"><div class="sk"></div><div class="sk"></div></div>`;
    try {
      const data = await api("/api/brief", { method: "POST", body: JSON.stringify({ query: q }) });
      $("#briefOut").innerHTML = `<h3>Mission brief</h3><div>${esc(data.brief || "")}</div><span class="provider-pill">provider: ${esc(data.provider)}</span>`;
      $("#briefEvidence").innerHTML =
        (data.evidence || []).map((h) => hitCard(h)).join("") || `<p class="empty">No evidence.</p>`;
      bindHits($("#briefEvidence"));
    } catch (err) {
      $("#briefOut").innerHTML = `<p class="empty">${esc(err.message)}</p>`;
      $("#briefEvidence").innerHTML = "";
      toast(err.message, true);
    }
  });

  let liveReadyUrl = "";

  function resetLiveStage() {
    const stage = $("#liveStage");
    const thumb = $("#liveThumb");
    const playBtn = $("#livePlayBtn");
    const video = $("#liveVideo");
    stage?.classList.remove("is-playing", "is-ready", "has-thumb");
    if (thumb) {
      thumb.hidden = true;
      thumb.removeAttribute("src");
    }
    if (playBtn) playBtn.hidden = true;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    liveReadyUrl = "";
  }

  function captureLiveThumbnail(video) {
    return new Promise((resolve) => {
      const finish = () => {
        try {
          if (!video.videoWidth) {
            resolve("");
            return;
          }
          const canvas = document.createElement("canvas");
          canvas.width = video.videoWidth;
          canvas.height = video.videoHeight;
          canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL("image/jpeg", 0.78));
        } catch {
          resolve("");
        }
      };
      const seekTo = Math.min(0.35, Math.max(0.05, (video.duration || 1) * 0.02));
      const onSeeked = () => {
        video.removeEventListener("seeked", onSeeked);
        finish();
      };
      if (video.readyState >= 2) {
        try {
          video.currentTime = seekTo;
          video.addEventListener("seeked", onSeeked);
          setTimeout(finish, 1200);
        } catch {
          finish();
        }
      } else {
        finish();
      }
    });
  }

  async function prepareLiveSource(source, { autoplay = false } = {}) {
    const video = $("#liveVideo");
    const sub = $("#liveSub");
    const stage = $("#liveStage");
    const thumb = $("#liveThumb");
    const playBtn = $("#livePlayBtn");
    if (!video || !source) return;
    resetLiveStage();
    sub.textContent = "Loading thumbnail…";
    try {
      const stream = await api(`/api/stream?source=${encodeURIComponent(source)}`);
      const playUrl = mediaUrl(stream.playback_url);
      liveReadyUrl = playUrl;
      video.muted = true;
      video.src = playUrl;
      video.preload = "auto";
      await new Promise((resolve, reject) => {
        const ok = () => {
          cleanup();
          resolve();
        };
        const bad = () => {
          cleanup();
          reject(new Error("Video failed to load"));
        };
        const cleanup = () => {
          video.removeEventListener("loadeddata", ok);
          video.removeEventListener("error", bad);
        };
        video.addEventListener("loadeddata", ok);
        video.addEventListener("error", bad);
        video.load();
      });
      const poster = await captureLiveThumbnail(video);
      video.pause();
      video.currentTime = 0;
      video.muted = false;
      if (poster && thumb) {
        thumb.src = poster;
        thumb.hidden = false;
        video.poster = poster;
        stage?.classList.add("has-thumb");
      }
      stage?.classList.add("is-ready");
      if (playBtn) playBtn.hidden = false;
      const fname = window.__FOCAL_NAME__ || "20261017_130940_210de363.mp4";
      sub.textContent = poster
        ? `Thumbnail ready for ${fname} — press play to watch`
        : `Preview loaded for ${fname} — press play to watch`;
      $("#replayLive").hidden = false;
      if (autoplay) startLivePlayback();
    } catch (e) {
      sub.textContent = `Could not load preview: ${e.message}`;
      toast(e.message, true);
    }
  }

  function startLivePlayback() {
    const video = $("#liveVideo");
    const stage = $("#liveStage");
    const sub = $("#liveSub");
    if (!video || !liveReadyUrl) return;
    stage?.classList.add("is-playing");
    video.src = liveReadyUrl;
    video.load();
    const p = video.play();
    if (p && typeof p.catch === "function") {
      p.catch((err) => {
        stage?.classList.remove("is-playing");
        toast(err?.message || "Playback blocked — press play on the video controls", true);
      });
    }
    const fname = window.__FOCAL_NAME__ || "20261017_130940_210de363.mp4";
    if (sub) sub.textContent = `Playing ${fname} — use controls to pause or seek`;
  }

  function wireLiveFootage(f) {
    const preview = f.preview_source;
    const timeline = f.timeline_sources || [];
    const wrap = $("#segJumpWrap");
    const sel = $("#segSelect");
    if (timeline.length && sel && wrap) {
      wrap.hidden = false;
      sel.innerHTML = timeline
        .map((t, i) => {
          const n = t.segment_number != null ? t.segment_number : i + 1;
          return `<option value="${esc(t.source)}">#${esc(n)}</option>`;
        })
        .join("");
      if (preview) sel.value = preview;
      sel.onchange = () => prepareLiveSource(sel.value);
    }
    $("#replayLive")?.addEventListener("click", () => {
      const src = sel?.value || preview;
      if (src) prepareLiveSource(src);
    });
    $("#livePlayBtn")?.addEventListener("click", startLivePlayback);
    $("#liveThumb")?.addEventListener("click", startLivePlayback);
    if (preview) prepareLiveSource(preview);
    else if (timeline[0]?.source) prepareLiveSource(timeline[0].source);
    else $("#liveSub").textContent = "No preview segment available for this footage.";
  }

  // Resolve focal footage status + mount live player
  api("/api/focal")
    .then((f) => {
      const el = $("#focalStats");
      const dot = $("#focalDot");
      const signals = $("#signalRow");
      const brief = $("#footageBrief");
      if (brief && f.brief) brief.textContent = f.brief;
      if (!f.found) {
        el.textContent = f.error || "not found in index";
        el.style.color = "var(--danger)";
        if (dot) {
          dot.classList.add("bad");
          dot.title = "Not found";
        }
        $("#liveSub").textContent = f.error || "Footage not found in index";
        toast(f.error || "Focal footage not found", true);
        return;
      }
      const segs = f.total_segments || f.timeline_len || "?";
      const name = f.filename || "20261017_130940_210de363.mp4";
      window.__FOCAL_NAME__ = name;
      el.textContent = `${segs} segments indexed · ready for mission search`;
      if (dot) {
        dot.classList.add("ok");
        dot.title = "Indexed";
      }
      if (signals) {
        signals.hidden = false;
        $$(".signal", signals).forEach((s) => s.classList.add("on"));
      }
      wireLiveFootage(f);
    })
    .catch((e) => {
      $("#focalStats").textContent = e.message;
      $("#focalDot")?.classList.add("bad");
      $("#liveSub").textContent = e.message;
      toast(e.message, true);
    });

  // Ensure panels start correctly when CSS loads
  activateTab("results");
})();
