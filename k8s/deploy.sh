#!/usr/bin/env bash
# Deploy AidSight Logistics app to team namespace at Ingress path /app
set -euo pipefail

mapfile -t TEAM_CONFIGS < <(find /config -maxdepth 1 -type f -name '*.config' | sort)
(( ${#TEAM_CONFIGS[@]} == 1 )) || { echo "expected exactly one /config/*.config"; exit 1; }
TEAM_CONFIG="${TEAM_CONFIGS[0]}"
# shellcheck disable=SC1090
set -a && source "$TEAM_CONFIG" && set +a

NS="$USERNAME"
export KUBECONFIG="/config/${NS}-k8s.yaml"
TEAM_N="${USERNAME#team-}"
APP_HOST="video-lab-team-${TEAM_N}.cosmos.vastdata.com"
APP_NAME="disaster-logistics"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_PORT=8080

echo "NS=$NS APP_HOST=$APP_HOST APP_DIR=$APP_DIR"

kubectl -n "$NS" create configmap "${APP_NAME}-code" \
  --from-file="${APP_DIR}/main.py" \
  --from-file="${APP_DIR}/requirements.txt" \
  --from-file="${APP_DIR}/index.html" \
  --from-file="${APP_DIR}/app.css" \
  --from-file="${APP_DIR}/app.js" \
  --dry-run=client -o yaml | kubectl apply -f -

# Prefer in-cluster backend DNS; external INGRESS_URL often does not resolve from pods.
VSS_IN_CLUSTER="${VSS_IN_CLUSTER:-http://video-backend-service:8000}"
kubectl -n "$NS" create secret generic "${APP_NAME}-vss-creds" \
  --from-literal=VSS_URL="$VSS_IN_CLUSTER" \
  --from-literal=VSS_USERNAME="$USERNAME" \
  --from-literal=VSS_PASSWORD="$PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

# Optional W&B key for mission briefs (if present in env)
if [[ -n "${WANDB_API_KEY:-}" ]]; then
  kubectl -n "$NS" create secret generic "${APP_NAME}-wandb" \
    --from-literal=WANDB_API_KEY="$WANDB_API_KEY" \
    --from-literal=WANDB_TEAM="${WANDB_TEAM:-}" \
    --from-literal=WANDB_PROJECT="${WANDB_PROJECT:-}" \
    --dry-run=client -o yaml | kubectl apply -f -
  WANDB_ENV=$(cat <<EOF
        - name: WANDB_API_KEY
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-wandb
              key: WANDB_API_KEY
              optional: true
        - name: WANDB_TEAM
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-wandb
              key: WANDB_TEAM
              optional: true
        - name: WANDB_PROJECT
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-wandb
              key: WANDB_PROJECT
              optional: true
EOF
)
else
  WANDB_ENV=""
fi

kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${APP_NAME}
  template:
    metadata:
      labels:
        app: ${APP_NAME}
    spec:
      containers:
      - name: app
        image: python:3.12-slim
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: ${APP_PORT}
        env:
        - name: PORT
          value: "${APP_PORT}"
        - name: VSS_URL
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_URL
        - name: VSS_USERNAME
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_USERNAME
        - name: VSS_PASSWORD
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_PASSWORD
        - name: VSS_PUBLIC_URL
          value: "https://${APP_HOST}"
${WANDB_ENV}
        volumeMounts:
        - name: code
          mountPath: /code
        workingDir: /code
        command: ["bash", "-c"]
        args:
        - |
          set -euo pipefail
          if [ -f requirements.txt ]; then
            pip install --no-cache-dir -q -r requirements.txt
          fi
          exec python main.py
        readinessProbe:
          httpGet:
            path: /health
            port: ${APP_PORT}
          initialDelaySeconds: 15
          periodSeconds: 10
      volumes:
      - name: code
        configMap:
          name: ${APP_NAME}-code
---
apiVersion: v1
kind: Service
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  selector:
    app: ${APP_NAME}
  ports:
  - name: http
    port: 80
    targetPort: ${APP_PORT}
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /\$2
spec:
  ingressClassName: nginx
  rules:
  - host: ${APP_HOST}
    http:
      paths:
      - path: /app(/|\$)(.*)
        pathType: ImplementationSpecific
        backend:
          service:
            name: ${APP_NAME}
            port:
              number: 80
EOF

kubectl -n "$NS" rollout restart deploy/"$APP_NAME" || true
kubectl -n "$NS" rollout status deploy/"$APP_NAME" --timeout=180s
kubectl -n "$NS" get pods,svc,ingress -l app="$APP_NAME"
echo "Open https://workshop.thecosmoslabs.com and click App"
curl -sS -o /dev/null -w "cluster /app HTTP %{http_code}\n" "http://${APP_HOST}/app" || true
curl -sS "http://${APP_HOST}/app/health" || true
echo
