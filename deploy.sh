#!/usr/bin/env bash
# Usage: PROJECT=.. GOOGLE_API_KEY=.. DT_TENANT=abc12345 DT_API_TOKEN=dt0c01.. ./deploy.sh cloudrun|gke
set -euo pipefail
: "${PROJECT:?}" "${GOOGLE_API_KEY:?}" "${DT_TENANT:?}" "${DT_API_TOKEN:?}"
TARGET="${1:?cloudrun or gke}"; REGION="${REGION:-us-central1}"; SERVICE="${SERVICE:-adk-agent-demo}"
gcloud services enable run.googleapis.com container.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com --project "$PROJECT"

if [ "$TARGET" = cloudrun ]; then
  put() { printf %s "$2" | gcloud secrets create "$1" --data-file=- --project "$PROJECT" 2>/dev/null || printf %s "$2" | gcloud secrets versions add "$1" --data-file=- --project "$PROJECT"; }
  put gemini-key "$GOOGLE_API_KEY"; put dt-otlp-headers "Authorization=Api-Token ${DT_API_TOKEN}"
  SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
  for s in gemini-key dt-otlp-headers; do
    gcloud secrets add-iam-policy-binding "$s" --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor --project "$PROJECT" >/dev/null
  done
  # CPU always allocated so the OTel exporter and background judge keep running between requests.
  gcloud run deploy "$SERVICE" --source . --project "$PROJECT" --region "$REGION" \
    --no-cpu-throttling --min-instances=1 --memory=1Gi --no-allow-unauthenticated \
    --set-env-vars "OTEL_EXPORTER_OTLP_ENDPOINT=https://${DT_TENANT}.live.dynatrace.com/api/v2/otlp,OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta,OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,ENV=cloudrun,PROJECT_ID=${PROJECT}" \
    --update-secrets "GOOGLE_API_KEY=gemini-key:latest,OTEL_EXPORTER_OTLP_HEADERS=dt-otlp-headers:latest"
  echo "Browser UI: gcloud run services proxy $SERVICE --region $REGION --project $PROJECT --port 8080"
else
  CLUSTER="${CLUSTER:-adk-demo}"; REPO="${REPO:-adk-demo}"; IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/adk-agent-demo:v4"
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION" --project "$PROJECT" 2>/dev/null || true
  gcloud container clusters describe "$CLUSTER" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1 || \
    gcloud container clusters create-auto "$CLUSTER" --region "$REGION" --project "$PROJECT"
  gcloud container clusters get-credentials "$CLUSTER" --region "$REGION" --project "$PROJECT"
  gcloud builds submit --tag "$IMAGE" --project "$PROJECT" .
  kubectl create secret generic dt-otlp --from-literal=DT_TENANT="$DT_TENANT" --from-literal=DT_API_TOKEN="$DT_API_TOKEN" \
    --from-literal=GOOGLE_API_KEY="$GOOGLE_API_KEY" --dry-run=client -o yaml | kubectl apply -f -
  sed "s#IMAGE_PLACEHOLDER#${IMAGE}#; s#PROJECT_ID_VALUE#${PROJECT}#" k8s.yaml | kubectl apply -f -
  kubectl rollout status deploy/adk-agent; echo "External IP: kubectl get svc adk-agent"
fi
