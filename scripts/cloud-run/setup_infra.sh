#!/usr/bin/env bash
# One-shot Cloud Run + Cloud SQL + Secret Manager + Scheduler bootstrap for
# Sands-sorter. Idempotent — re-running only fills in what's missing.
#
# Usage:
#   GCP_PROJECT_ID=my-project ./scripts/cloud-run/setup_infra.sh
#
# Optional overrides (sane defaults assumed otherwise):
#   REGION=australia-southeast1
#   SQL_INSTANCE=sands-sql
#   SQL_DB=sands
#   SQL_USER=sands
#   SERVICE=sands-sorter
#   SCHEDULER_REGION=australia-southeast1
#
# Requires: gcloud (logged in as an owner/editor), openssl.

set -euo pipefail

PROJECT="${GCP_PROJECT_ID:?GCP_PROJECT_ID env var is required}"
REGION="${REGION:-australia-southeast1}"
SCHEDULER_REGION="${SCHEDULER_REGION:-$REGION}"
SQL_INSTANCE="${SQL_INSTANCE:-sands-sql}"
SQL_DB="${SQL_DB:-sands}"
SQL_USER="${SQL_USER:-sands}"
SERVICE="${SERVICE:-sands-sorter}"
SERVICE_ACCOUNT="${SERVICE}-sa@${PROJECT}.iam.gserviceaccount.com"
SCHEDULER_SA="${SERVICE}-scheduler@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT" >/dev/null

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  documentai.googleapis.com \
  iam.googleapis.com

echo "==> Service accounts"
gcloud iam service-accounts describe "$SERVICE_ACCOUNT" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "${SERVICE}-sa" --display-name "Sands-sorter runtime"
gcloud iam service-accounts describe "$SCHEDULER_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create "${SERVICE}-scheduler" --display-name "Sands-sorter Scheduler invoker"

for role in roles/cloudsql.client roles/secretmanager.secretAccessor roles/documentai.apiUser; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SERVICE_ACCOUNT}" --role="$role" --condition=None >/dev/null
done
# Scheduler SA gets the right to invoke the Cloud Run service we'll deploy.
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SCHEDULER_SA}" --role="roles/run.invoker" --condition=None >/dev/null

echo "==> Cloud SQL Postgres instance"
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="$REGION" \
    --storage-size=10GB \
    --backup-start-time=02:00
fi

if ! gcloud sql databases list --instance="$SQL_INSTANCE" --format="value(name)" \
     | grep -qx "$SQL_DB"; then
  gcloud sql databases create "$SQL_DB" --instance="$SQL_INSTANCE"
fi

# Generate or reuse the DB password secret.
if ! gcloud secrets describe sql-password >/dev/null 2>&1; then
  pw="$(openssl rand -base64 24)"
  printf '%s' "$pw" | gcloud secrets create sql-password --data-file=-
  gcloud sql users create "$SQL_USER" --instance="$SQL_INSTANCE" --password="$pw" 2>/dev/null \
    || gcloud sql users set-password "$SQL_USER" --instance="$SQL_INSTANCE" --password="$pw"
else
  echo "    sql-password secret already exists — leaving the DB user as-is"
fi

INSTANCE_CONN="${PROJECT}:${REGION}:${SQL_INSTANCE}"
DATABASE_URL="postgresql+psycopg://${SQL_USER}:__SQL_PW__@/${SQL_DB}?host=/cloudsql/${INSTANCE_CONN}"
echo "    Cloud SQL connection name: $INSTANCE_CONN"

echo "==> Secret Manager: app secrets"
ensure_secret() {
  local name="$1"
  if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
    gcloud secrets create "$name" --replication-policy=automatic
    echo "    created empty secret '$name' — populate it with: gcloud secrets versions add $name --data-file=path/to/value"
  fi
}
ensure_secret drive-token
ensure_secret anthropic-api-key
ensure_secret dropbox-refresh-token
ensure_secret internal-api-key
ensure_secret session-secret-key
ensure_secret oauth-client-id
ensure_secret oauth-client-secret

# Auto-populate the random ones if they're empty.
for s in internal-api-key session-secret-key; do
  versions=$(gcloud secrets versions list "$s" --filter="state=enabled" --format="value(name)" 2>/dev/null || true)
  if [ -z "$versions" ]; then
    openssl rand -hex 32 | gcloud secrets versions add "$s" --data-file=-
    echo "    populated $s with a random 32-byte hex value"
  fi
done

cat <<MSG

==========================================================
Bootstrap complete.

Next:
  1. Get the Drive+Gmail OAuth token (helper runs the consent flow):
       python scripts/auth/get_token.py \\
         --client-secrets ~/Downloads/client_secret.json \\
         --scopes drive,gmail-modify,gmail-send \\
         --upload-to-secret drive-token \\
         --gcp-project $PROJECT

     Populate the rest:
       gcloud secrets versions add anthropic-api-key   --data-file=- <<<"sk-ant-..."
       gcloud secrets versions add dropbox-refresh-token --data-file=- <<<"sl.your-token"
       gcloud secrets versions add oauth-client-id     --data-file=- <<<"...apps.googleusercontent.com"
       gcloud secrets versions add oauth-client-secret --data-file=- <<<"GOCSPX-..."

  2. Build + deploy:
       ./scripts/cloud-run/deploy.sh

  3. (After the first deploy) wire Cloud Scheduler:
       SERVICE_URL=\$(gcloud run services describe $SERVICE --region $REGION --format='value(status.url)')
       INTERNAL_KEY=\$(gcloud secrets versions access latest --secret=internal-api-key)
       gcloud scheduler jobs create http poll-sources \\
         --location=$SCHEDULER_REGION \\
         --schedule="*/15 * * * *" \\
         --time-zone="Australia/Sydney" \\
         --uri="\$SERVICE_URL/internal/poll" \\
         --http-method=POST \\
         --oidc-service-account-email=$SCHEDULER_SA \\
         --headers="X-Internal-Key=\$INTERNAL_KEY"
       gcloud scheduler jobs create http summary-digest \\
         --location=$SCHEDULER_REGION \\
         --schedule="0 7 * * 1" \\
         --time-zone="Australia/Sydney" \\
         --uri="\$SERVICE_URL/internal/summary" \\
         --http-method=POST \\
         --oidc-service-account-email=$SCHEDULER_SA \\
         --headers="X-Internal-Key=\$INTERNAL_KEY"
==========================================================
MSG
