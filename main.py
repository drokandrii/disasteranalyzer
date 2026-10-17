"""Disaster Response & Humanitarian Logistics Analyzer — single-footage Mission Ops app."""

from __future__ import annotations

import os
import re
import threading
from typing import Any
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

FOCAL_FILENAME = "20261017_130940_210de363.mp4"
FOCAL_VIDEO_DEFAULT = "s3://team-3-vss-chunks/team-3/20261017_130940_210de363.mp4"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FOOTAGE_BRIEF = (
    "Aerial rural corridor with active wildfire: smoke plumes along a road, "
    "vehicle convoy traffic, fire truck and emergency personnel near the blaze, "
    "charred ground, and limited civilian movement on dirt access roads."
)

OPS_COLUMNS = [
    {
        "id": "supply_routes",
        "title": "Supply Routes",
        "query": "road vehicles convoy traffic smoke near roadway rural corridor",
    },
    {
        "id": "fire_response",
        "title": "Fire Response",
        "query": "fire truck wildfire smoke plume emergency personnel burning vegetation",
    },
    {
        "id": "access_people",
        "title": "Access & People",
        "query": "person walking dirt road near smoke firefighters emergency access",
    },
]

SUGGESTED_CHIPS = [
    "wildfire smoke near the road",
    "fire truck at the blaze",
    "vehicles convoy on rural road",
    "emergency personnel near fire",
    "person on dirt road near smoke",
    "charred burn scar landscape",
]

app = Flask(__name__, static_folder=None)

_token: str | None = None
_token_lock = threading.Lock()
_focal_cache: dict[str, Any] = {}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def vss_base() -> str:
    return _env("VSS_URL", _env("INGRESS_URL")).rstrip("/")


def vss_public_base() -> str:
    """Browser-reachable VSS base (stream/playback). Falls back to request host."""
    explicit = _env("VSS_PUBLIC_URL")
    if explicit:
        return explicit.rstrip("/")
    # Workshop hosts the VSS UI/API on the team ingress host.
    host = request.host.split(":")[0] if request else ""
    if host:
        return f"https://{host}"
    return vss_base()


def login(force: bool = False) -> str:
    global _token
    with _token_lock:
        if _token and not force:
            return _token
        url = f"{vss_base()}/api/v1/auth/login"
        r = requests.post(
            url,
            json={
                "username": _env("VSS_USERNAME", _env("USERNAME")),
                "password": _env("VSS_PASSWORD", _env("PASSWORD")),
            },
            timeout=30,
        )
        r.raise_for_status()
        _token = r.json()["access_token"]
        return _token


def vss(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None, retry: bool = True) -> Any:
    headers = {"Authorization": f"Bearer {login()}"}
    url = f"{vss_base()}{path}"
    r = requests.request(method, url, headers=headers, json=json_body, params=params, timeout=120)
    if r.status_code == 401 and retry:
        login(force=True)
        return vss(method, path, json_body=json_body, params=params, retry=False)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    if not r.content:
        return {}
    return r.json()


def matches_focal(value: Any) -> bool:
    if value is None:
        return False
    s = value if isinstance(value, str) else str(value)
    return FOCAL_FILENAME in s


def focal_original_video() -> str:
    if _focal_cache.get("original_video"):
        return _focal_cache["original_video"]
    resolve_focal()
    return _focal_cache.get("original_video") or FOCAL_VIDEO_DEFAULT


def resolve_focal() -> dict[str, Any]:
    if _focal_cache.get("resolved"):
        return _focal_cache
    offset = 0
    match = None
    total = 0
    while True:
        data = vss("GET", "/api/v1/videos/explore", params={"scope": "all", "limit": 100, "offset": offset}) or {}
        total = int(data.get("total") or 0)
        chunks = data.get("chunks") or []
        for ch in chunks:
            if matches_focal(ch.get("original_video")) or matches_focal(ch.get("filename")):
                match = ch
                break
        if match or not chunks or offset + 100 >= total:
            break
        offset += 100

    if not match:
        _focal_cache.update(
            {
                "resolved": True,
                "found": False,
                "original_video": FOCAL_VIDEO_DEFAULT,
                "filename": FOCAL_FILENAME,
                "error": f"{FOCAL_FILENAME} not found in explore",
            }
        )
        return _focal_cache

    preview = match.get("preview_source")
    timeline = match.get("timeline") or []
    timeline_sources = []
    for item in timeline:
        if isinstance(item, dict) and item.get("source"):
            timeline_sources.append(
                {
                    "source": item.get("source"),
                    "segment_number": item.get("segment_number") or item.get("segment"),
                    "start_sec": item.get("start_sec") or item.get("start_time"),
                    "end_sec": item.get("end_sec") or item.get("end_time"),
                }
            )
    _focal_cache.update(
        {
            "resolved": True,
            "found": True,
            "original_video": match.get("original_video") or FOCAL_VIDEO_DEFAULT,
            "filename": match.get("filename") or FOCAL_FILENAME,
            "camera_id": match.get("camera_id"),
            "location": match.get("location"),
            "capture_type": match.get("capture_type"),
            "total_segments": match.get("total_segments"),
            "preview_source": preview,
            "preview_stream_url": f"/api/stream?source={quote(preview, safe='')}" if preview else None,
            "upload_timestamp": match.get("upload_timestamp"),
            "timeline_len": len(timeline),
            "timeline_sources": timeline_sources[:30],
        }
    )
    return _focal_cache


def filter_results(results: list[dict]) -> list[dict]:
    return [r for r in results if matches_focal(r.get("original_video")) or matches_focal(r.get("source"))]


def filter_chunks(chunks: list[dict]) -> list[dict]:
    return [c for c in chunks if matches_focal(c.get("original_video"))]


def summarize_detections(det: dict | None) -> dict[str, Any]:
    if not det:
        return {"available": False}
    return {
        "available": True,
        "object_classes": det.get("object_classes") or [],
        "object_counts": det.get("object_counts") or {},
        "detection_count": det.get("detection_count"),
        "source": det.get("source"),
        "video_shape": det.get("video_shape"),
        "fps": det.get("fps"),
    }


def _segment_label(source: str | None) -> str | None:
    if not source:
        return None
    # e.g. .../20261017_..._segment_011_of_030.mp4
    m = re.search(r"segment_(\d+)_of_(\d+)", source)
    if m:
        return f"segment {int(m.group(1))}/{int(m.group(2))}"
    return None


def enrich_hit(hit: dict) -> dict:
    src = hit.get("source") or hit.get("preview_source")
    start = hit.get("start_time") or hit.get("start_sec") or hit.get("best_match_start_sec")
    end = hit.get("end_time") or hit.get("end_sec") or hit.get("best_match_end_sec")
    out = {
        "source": src,
        "original_video": hit.get("original_video"),
        "similarity_score": hit.get("similarity_score"),
        "reasoning_content": hit.get("reasoning_content"),
        "start_sec": start,
        "end_sec": end,
        "segment_label": _segment_label(src if isinstance(src, str) else None),
        "camera_id": hit.get("camera_id"),
        "location": hit.get("location"),
        "matched_segment_count": hit.get("matched_segment_count"),
    }
    if src:
        out["stream_url"] = f"/api/stream?source={quote(src, safe='')}"
    return out


@app.get("/health")
def health():
    return jsonify({"ok": True, "focal": FOCAL_FILENAME, "brief": FOOTAGE_BRIEF})


@app.get("/static/<path:filename>")
def static_files(filename: str):
    # Prefer flat ConfigMap layout (app.css / app.js next to main.py)
    flat = os.path.join(BASE_DIR, filename)
    if os.path.isfile(flat):
        return send_from_directory(BASE_DIR, filename)
    nested = os.path.join(BASE_DIR, "static", filename)
    if os.path.isfile(nested):
        return send_from_directory(os.path.join(BASE_DIR, "static"), filename)
    return ("not found", 404)


@app.get("/")
@app.get("/index.html")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/focal")
def api_focal():
    try:
        info = dict(resolve_focal())
        info["brief"] = FOOTAGE_BRIEF
        info["chips"] = SUGGESTED_CHIPS
        info["ops_columns"] = OPS_COLUMNS
        return jsonify(info)
    except Exception as exc:  # noqa: BLE001
        return jsonify(
            {
                "found": False,
                "filename": FOCAL_FILENAME,
                "original_video": FOCAL_VIDEO_DEFAULT,
                "brief": FOOTAGE_BRIEF,
                "error": f"{type(exc).__name__}: {exc}",
            }
        ), 503


@app.post("/api/search")
def api_search():
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    top_k = int(body.get("top_k") or 20)
    min_sim = float(body.get("min_similarity") or 0.12)
    payload = {
        "query": query,
        "top_k": min(max(top_k, 1), 50),
        "llm_top_n": int(body.get("llm_top_n") or 3),
        "min_similarity": min_sim,
        "time_filter": body.get("time_filter") or "all",
        "include_public": True,
    }
    data = vss("POST", "/api/v1/search", json_body=payload) or {}
    results = filter_results(data.get("results") or [])
    chunks = filter_chunks(data.get("chunk_results") or [])
    # Soft fallback: logistics phrasing may score low against general captions
    used_sim = min_sim
    if not results and min_sim > 0.05:
        used_sim = 0.05
        payload["min_similarity"] = used_sim
        data = vss("POST", "/api/v1/search", json_body=payload) or {}
        results = filter_results(data.get("results") or [])
        chunks = filter_chunks(data.get("chunk_results") or [])
    return jsonify(
        {
            "query": query,
            "focal_video": focal_original_video(),
            "results": [enrich_hit(r) for r in results],
            "chunk_results": [enrich_hit(c) for c in chunks],
            "llm_synthesis": data.get("llm_synthesis"),
            "raw_total": len(data.get("results") or []),
            "focal_total": len(results),
            "min_similarity": used_sim,
        }
    )


@app.post("/api/ask")
def api_ask():
    body = request.get_json(force=True, silent=True) or {}
    question = (body.get("question") or body.get("query") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    ov = focal_original_video()
    # Prefer video-scoped ask
    data = vss(
        "POST",
        "/api/v1/agent/ask",
        json_body={"question": question, "original_video": ov, "top_k": int(body.get("top_k") or 12)},
    )
    if data is None:
        data = {}
    evidence = data.get("evidence") or {}
    clips = []
    if isinstance(evidence, dict):
        for key in ("chunks", "segments", "results"):
            for item in evidence.get(key) or []:
                if isinstance(item, dict):
                    clips.append(enrich_hit(item))
    # Agent /ask often returns only usage counts — backfill clips via search scoped to focal
    if not clips:
        search = vss(
            "POST",
            "/api/v1/search",
            json_body={
                "query": question,
                "top_k": 15,
                "llm_top_n": 3,
                "min_similarity": 0.15,
                "time_filter": "all",
                "include_public": True,
            },
        ) or {}
        clips = [enrich_hit(r) for r in filter_results(search.get("results") or [])][:8]
    return jsonify(
        {
            "question": question,
            "answer": data.get("answer"),
            "tool_used": data.get("tool_used"),
            "evidence_clips": clips,
            "evidence": evidence,
            "focal_video": ov,
        }
    )


@app.get("/api/clip")
def api_clip():
    source = request.args.get("source") or ""
    if not source:
        return jsonify({"error": "source required"}), 400
    if not matches_focal(source) and FOCAL_FILENAME not in source:
        # allow segment URIs that belong to focal even if filename pattern differs slightly
        pass
    meta = vss("GET", "/api/v1/videos/metadata", params={"source": source})
    det = vss("GET", "/api/v1/videos/detections", params={"source": source})
    if meta and meta.get("original_video") and not matches_focal(meta.get("original_video")):
        return jsonify({"error": "clip is outside focal footage"}), 403
    return jsonify(
        {
            "source": source,
            "metadata": meta,
            "detections": summarize_detections(det),
            "detections_raw": det if det else None,
            "stream_url": f"/api/stream?source={quote(source, safe='')}",
            "reasoning_content": (meta or {}).get("reasoning_content"),
        }
    )


@app.get("/api/stream")
def api_stream():
    """Always return a same-origin proxy URL.

    Presigned VSS/S3 URLs are internal HTTP hosts that break in the browser
    (mixed content / unreachable from the workshop app origin).
    """
    source = request.args.get("source") or ""
    if not source:
        return jsonify({"error": "source required"}), 400
    return jsonify(
        {
            "playback_url": f"/api/playback?source={quote(source, safe='')}",
            "source": source,
            "mode": "proxy",
        }
    )


@app.get("/api/playback")
def api_playback():
    """Proxy VSS segment bytes so <video> stays on the workshop app origin."""
    source = request.args.get("source") or ""
    if not source:
        return jsonify({"error": "source required"}), 400
    token = login()
    upstream = (
        f"{vss_base()}/api/v1/videos/stream"
        f"?source={quote(source, safe='')}&token={quote(token)}"
    )
    headers = {}
    range_hdr = request.headers.get("Range")
    if range_hdr:
        headers["Range"] = range_hdr
    try:
        upstream_resp = requests.get(upstream, headers=headers, stream=True, timeout=120)
    except requests.RequestException as exc:
        return jsonify({"error": f"upstream stream failed: {exc}"}), 502

    if upstream_resp.status_code >= 400:
        body = upstream_resp.text[:300]
        upstream_resp.close()
        return jsonify({"error": f"upstream {upstream_resp.status_code}: {body}"}), upstream_resp.status_code

    excluded = {"content-encoding", "transfer-encoding", "connection"}
    out_headers = {
        k: v
        for k, v in upstream_resp.headers.items()
        if k.lower() not in excluded
    }
    out_headers.setdefault("Accept-Ranges", "bytes")
    out_headers.setdefault("Cache-Control", "private, max-age=60")

    def generate():
        try:
            for chunk in upstream_resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream_resp.close()

    # Force a playable media type — upstream sometimes returns octet-stream.
    out_headers["Content-Type"] = "video/mp4"
    return Response(
        generate(),
        status=upstream_resp.status_code,
        headers=out_headers,
        mimetype="video/mp4",
        direct_passthrough=True,
    )


@app.get("/api/ops-board")
def api_ops_board():
    columns = []
    for col in OPS_COLUMNS:
        data = vss(
            "POST",
            "/api/v1/search",
            json_body={
                "query": col["query"],
                "top_k": 12,
                "llm_top_n": 2,
                "min_similarity": 0.1,
                "time_filter": "all",
                "include_public": True,
            },
        ) or {}
        hits = [enrich_hit(r) for r in filter_results(data.get("results") or [])][:8]
        columns.append(
            {
                "id": col["id"],
                "title": col["title"],
                "query": col["query"],
                "hits": hits,
                "synthesis": (data.get("llm_synthesis") or {}).get("response"),
            }
        )
    return jsonify({"focal_video": focal_original_video(), "columns": columns})


@app.post("/api/synthesize")
def api_synthesize():
    body = request.get_json(force=True, silent=True) or {}
    question = (
        body.get("question")
        or "Summarize wildfire logistics signals: smoke vs road, vehicle convoy, fire truck response, and access risks."
    ).strip()
    data = vss(
        "POST",
        "/api/v1/videos/synthesize",
        json_body={
            "original_video": focal_original_video(),
            "question": question,
            "max_segments": int(body.get("max_segments") or 40),
            "system_prompt": body.get("system_prompt")
            or (
                "You are a disaster-response logistics analyst reviewing aerial wildfire footage. "
                "Produce a concise mission brief: corridor/route status, smoke and fire urgency, "
                "emergency response assets, and civilian/personnel access risks. "
                "Use only the provided segment evidence."
            ),
        },
    )
    return jsonify({"focal_video": focal_original_video(), "result": data})


@app.post("/api/brief")
def api_brief():
    """Draft a coordinator brief via W&B serverless inference over focal evidence."""
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "aerial wildfire logistics assessment").strip()
    search = vss(
        "POST",
        "/api/v1/search",
        json_body={
            "query": query,
            "top_k": 30,
            "llm_top_n": 3,
            "min_similarity": 0.12,
            "time_filter": "all",
            "include_public": True,
        },
    ) or {}
    hits = filter_results(search.get("results") or [])[:8]
    if not hits:
        search2 = vss(
            "POST",
            "/api/v1/search",
            json_body={
                "query": "fire smoke vehicles people road congestion debris emergency response",
                "top_k": 30,
                "llm_top_n": 2,
                "min_similarity": 0.1,
                "time_filter": "all",
                "include_public": True,
            },
        ) or {}
        hits = filter_results(search2.get("results") or [])[:8]
    evidence_lines = []
    for i, h in enumerate(hits, 1):
        reason = (h.get("reasoning_content") or "").strip().replace("\n", " ")
        score = h.get("similarity_score")
        score_s = f"{score:.3f}" if isinstance(score, (int, float)) else str(score)
        evidence_lines.append(f"{i}. score={score_s}: {reason[:400]}")
    ask_for_brief = vss(
        "POST",
        "/api/v1/agent/ask",
        json_body={"question": query, "original_video": focal_original_video(), "top_k": 10},
    ) or {}
    agent_ctx = ask_for_brief.get("answer") or ""
    evidence_block = "\n".join(evidence_lines) or "(no focal search hits)"
    if agent_ctx:
        evidence_block = f"Agent summary:\n{agent_ctx}\n\nSegment captions:\n{evidence_block}"

    api_key = _env("WANDB_API_KEY")
    brief_text = None
    provider = None
    if api_key:
        try:
            model = _env("WANDB_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
            resp = requests.post(
                "https://api.inference.wandb.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a disaster-response logistics coordinator "
                                "reviewing aerial wildfire footage. Write a short mission brief "
                                "(fire urgency, road/corridor status, emergency assets, "
                                "access risks, recommended next look). "
                                "Use only the evidence. Be concrete and concise."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Footage: {FOCAL_FILENAME}\n"
                                f"Scene context: {FOOTAGE_BRIEF}\n"
                                f"Focus query: {query}\n"
                                f"Evidence segments:\n{evidence_block}"
                            ),
                        },
                    ],
                    "temperature": 0.2,
                    "max_tokens": 500,
                },
                timeout=60,
            )
            if resp.ok:
                brief_text = resp.json()["choices"][0]["message"]["content"]
                provider = "wandb"
            else:
                brief_text = None
                provider = f"wandb_error_{resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            provider = f"wandb_exception:{type(exc).__name__}"
            brief_text = None

    if not brief_text:
        # Fallback: use VSS synthesize / search synthesis
        synth = (search.get("llm_synthesis") or {}).get("response")
        if synth:
            brief_text = synth
            provider = provider or "vss_llm_synthesis"
        else:
            syn = vss(
                "POST",
                "/api/v1/videos/synthesize",
                json_body={
                    "original_video": focal_original_video(),
                    "question": f"Mission brief for: {query}",
                    "max_segments": 30,
                },
            ) or {}
            brief_text = syn.get("answer") or "Insufficient evidence to draft a brief."
            provider = provider or "vss_synthesize"

    return jsonify(
        {
            "query": query,
            "brief": brief_text,
            "provider": provider,
            "evidence": [enrich_hit(h) for h in hits],
            "focal_video": focal_original_video(),
        }
    )


def _warmup():
    try:
        login()
        resolve_focal()
    except Exception:
        pass


if __name__ == "__main__":
    port = int(_env("PORT", "8080") or 8080)
    threading.Thread(target=_warmup, daemon=True).start()
    app.run(host="0.0.0.0", port=port, debug=False)
