"""Деплой/обновление Cloud Run через REST API v2 (глобальный endpoint run.googleapis.com)."""
import json, subprocess, sys, time, os
import httpx
PROJECT, REGION, SERVICE = "dealducks", "europe-west1", "cqa"
image = sys.argv[1]
env = {k: v for k, v in os.environ.items() if k != "CLOUDSDK_AUTH_ACCESS_TOKEN"}
tok = subprocess.check_output(["gcloud", "auth", "print-access-token"], env=env, text=True).strip()
conn = subprocess.check_output(["gcloud", "sql", "instances", "describe", "cqa-pg", "--format=value(connectionName)"], env=env, text=True).strip()
H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
base = f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/services"
plain = {"CRITERIA_PROVIDER": "openai", "ANNOTATION_PROVIDER": "gemini", "DEFAULT_GROUP_ID": "DEV",
         "DAILY_BUDGET_USD": "20", "RATE_LIMIT_PER_MIN": "30", "BATCH_CONCURRENCY": "8"}
secrets = {"OPENAI_API_KEY": "cqa-openai-key", "GEMINI_API_KEY": "cqa-gemini-key",
           "DATABASE_URL": "cqa-database-url", "CQA_API_URL": "cqa-data-api-url"}
envs = [{"name": k, "value": v} for k, v in plain.items()] + \
       [{"name": k, "valueSource": {"secretKeyRef": {"secret": s, "version": "latest"}}} for k, s in secrets.items()]
body = {"ingress": "INGRESS_TRAFFIC_ALL", "template": {
    "timeout": "3600s", "maxInstanceRequestConcurrency": 80,
    "scaling": {"minInstanceCount": 0, "maxInstanceCount": 3},
    "volumes": [{"name": "cloudsql", "cloudSqlInstance": {"instances": [conn]}}],
    "containers": [{"image": image, "ports": [{"containerPort": 8080}], "env": envs,
                    "resources": {"limits": {"cpu": "1", "memory": "1Gi"}, "cpuIdle": False, "startupCpuBoost": True},
                    "volumeMounts": [{"name": "cloudsql", "mountPath": "/cloudsql"}]}]}}
c = httpx.Client(timeout=60)
exists = c.get(f"{base}/{SERVICE}", headers=H).status_code == 200
r = (c.patch(f"{base}/{SERVICE}", headers=H, json=body) if exists else c.post(f"{base}?serviceId={SERVICE}", headers=H, json=body))
print("deploy:", r.status_code, r.text[:300] if r.status_code >= 300 else "")
r.raise_for_status()
op = r.json()["name"]
while True:
    o = c.get(f"https://run.googleapis.com/v2/{op}", headers=H).json()
    if o.get("done"):
        if "error" in o: print("ERROR", o["error"]); sys.exit(1)
        break
    time.sleep(5)
pol = c.post(f"{base}/{SERVICE}:setIamPolicy", headers=H, json={"policy": {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}})
print("public:", pol.status_code, pol.text[:200] if pol.status_code >= 300 else "ok")
svc = c.get(f"{base}/{SERVICE}", headers=H).json()
print("URL", svc.get("uri"))
print("revision", svc.get("latestReadyRevision"), "conditions", [(x.get("type"), x.get("state")) for x in svc.get("conditions", [])][:3])
