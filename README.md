# AidSight Logistics — Disaster Response & Humanitarian Logistics Analyzer

VAST Builders Challenge app that analyzes a single aerial wildfire footage asset with YOLO detections, Cosmos Reason captions, and hybrid semantic search.

## Focal footage

`20261017_130940_210de363.mp4` — aerial rural corridor with wildfire smoke, road traffic, fire truck response, and emergency personnel.

## Stack

- Flask app on Kubernetes Ingress `/app`
- VSS retrieval APIs (search, agent Q&A, videos, detections)
- Optional Weights & Biases serverless inference for mission briefs

## Local layout

```
main.py              # Flask API + VSS client
index.html           # Mission Ops UI
app.css / app.js     # Styles and client logic
static/              # Served assets
k8s/deploy.sh        # Hackathon ConfigMap deploy (no registry)
requirements.txt
```

## Deploy (Builders Challenge VM)

```bash
bash k8s/deploy.sh
```

Then open [workshop.thecosmoslabs.com](https://workshop.thecosmoslabs.com) → **App**, or your team app URL.

## Security

Do not commit `/config/*.config`, passwords, or API keys. The deploy script reads credentials from the lab environment only.
