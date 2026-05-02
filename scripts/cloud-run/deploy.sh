#!/usr/bin/env bash
# Build the image with Cloud Build and deploy to Cloud Run.
# Re-runnable on every code change.
#
# Usage:
#   GCP_PROJECT_ID=my-project ./scripts/cloud-run/deploy.sh
#
# Requires setup_infra.sh to have been run once.

set -euo pipefail

PROJECT="${GCP_PROJECT_ID:?GCP_PROJECT_ID env var is required}"
REGION="${REGION:-australia-southeast1}"
SQL_INSTANCE="${SQL_INSTANCE:-sands-sql}"
SQL_DB="${SQL_DB:-sands}"
SQL_USER="${SQL_USER:-sands}"
SERVICE="${SERVICE:-sands-sorter}"
ALLOWED_EMAILS="${ALLOWED_EMAILS:?Set ALLOWED_EMAILS to a comma-separated list of Google emails allowed to log in}"
DRIVE_ROOT_FOLDER_ID="${DRIVE_ROOT_FOLDER_ID:?Set DRIVE_ROOT_FOLDER_ID to your Drive root folder id}"
DOCUMENT_AI_PROCESSOR_ID="${DOCUMENT_AI_PROCESSOR_ID:-}"
DOCUMENT_AI_LOCATION="${DOCUMENT_AI_LOCATION:-us}"

INSTANCE_CONN="${PROJECT}:${REGION}:${SQL_INSTANCE}"
SERVICE_ACCOUNT="${SERVICE}-sa@${PROJECT}.iam.gserviceaccount.com"
IMAGE="gcr.io/${PROJECT}/${SERVICE}:$(date +%Y%m%d-%H%M%S)"

gcloud config set project "$PROJECT" >/dev/null

echo "==> Building image with Cloud Build: $IMAGE"
gcloud builds submit --tag "$IMAGE" .

# Pull the SQL password fresh from Secret Manager so we never echo it.
SQL_PASSWORD=$(gcloud secrets versions access latest --secret=sql-password)

echo "==> Deploying Cloud Run service: $SERVICE"
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --service-account="$SERVICE_ACCOUNT" \
  --allow-unauthenticated \
  --add-cloudsql-instances="$INSTANCE_CONN" \
  --memory=1Gi \
  --cpu=1 \
  --concurrency=20 \
  --min-instances=0 \
  --max-instances=4 \
  --set-env-vars="ENV=prod" \
  --set-env-vars="ALLOWED_EMAILS=${ALLOWED_EMAILS}" \
  --set-env-vars="DRIVE_ROOT_FOLDER_ID=${DRIVE_ROOT_FOLDER_ID}" \
  --set-env-vars="GCP_PROJECT_ID=${PROJECT}" \
  --set-env-vars="DOCUMENT_AI_PROCESSOR_ID=${DOCUMENT_AI_PROCESSOR_ID}" \
  --set-env-vars="DOCUMENT_AI_LOCATION=${DOCUMENT_AI_LOCATION}" \
  --set-env-vars="DATABASE_URL=postgresql+psycopg://${SQL_USER}:${SQL_PASSWORD}@/${SQL_DB}?host=/cloudsql/${INSTANCE_CONN}" \
  --set-secrets="DRIVE_OAUTH_TOKEN_JSON=drive-token:latest" \
  --set-secrets="ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  --set-secrets="DROPBOX_REFRESH_TOKEN=dropbox-refresh-token:latest" \
  --set-secrets="INTERNAL_API_KEY=internal-api-key:latest" \
  --set-secrets="SECRET_KEY=session-secret-key:latest" \
  --set-secrets="GOOGLE_OAUTH_CLIENT_ID=oauth-client-id:latest" \
  --set-secrets="GOOGLE_OAUTH_CLIENT_SECRET=oauth-client-secret:latest"

URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')
echo
echo "Deployed: $URL"
echo "Health check: $URL/healthz"
