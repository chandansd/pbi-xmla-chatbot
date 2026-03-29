"""
Test script: validates the XmlaRunner + Power BI XMLA setup end-to-end.

Steps:
  1. Get Azure AD bearer token (client credentials flow)
  2. Start XmlaRunner service
  3. Health check
  4. Run a simple DAX query
"""

import os, subprocess, time, json, urllib.request, urllib.parse, urllib.error

# ── Config (from .env) ─────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "PBI_XMLA_Chatbot_Package", "Chatbot", ".env"))

TENANT_ID     = os.environ["TENANT_ID"]
CLIENT_ID     = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
WORKSPACE     = os.getenv("WORKSPACE", "AITests")
DATASET       = os.getenv("DATASET",   "BournoutPBIDashboard")
XMLA_API      = "http://localhost:5000/run-dax"
HEALTH_URL    = "http://localhost:5000/"

RUNNER_EXE = os.path.abspath(
    r"PBI_XMLA_Chatbot_Package\XmlaRunner\bin\Debug\net48\XmlaRunner.exe"
)

# Simple DAX — always works if connection succeeds
TEST_DAX = "EVALUATE ROW(\"Result\", 1)"

SEP = "-" * 60

def step(n, msg):
    print(f"\n{SEP}\nSTEP {n}: {msg}\n{SEP}")

def ok(msg):   print(f"  [OK]   {msg}")
def fail(msg): print(f"  [FAIL] {msg}")

# ── Step 1: Verify credentials can get a token ────────────────────────────────
step(1, "Verify Azure AD credentials (client credentials flow)")

token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
data = urllib.parse.urlencode({
    "grant_type":    "client_credentials",
    "client_id":     CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "scope":         "https://analysis.windows.net/powerbi/api/.default",
}).encode()

try:
    with urllib.request.urlopen(token_url, data=data, timeout=15) as r:
        token_resp = json.loads(r.read())
    if not token_resp.get("access_token"):
        fail(f"No access_token in response: {token_resp}")
        exit(1)
    ok(f"Credentials valid (token expires in {token_resp.get('expires_in')}s)")
except Exception as e:
    fail(f"Credential check failed: {e}")
    exit(1)

# ── Step 2: Start XmlaRunner ───────────────────────────────────────────────────
step(2, f"Start XmlaRunner\n  {RUNNER_EXE}")

if not os.path.exists(RUNNER_EXE):
    fail(f"Executable not found: {RUNNER_EXE}")
    exit(1)

env = os.environ.copy()
env["TENANT_ID"]     = TENANT_ID
env["CLIENT_ID"]     = CLIENT_ID
env["CLIENT_SECRET"] = CLIENT_SECRET

proc = subprocess.Popen(
    [RUNNER_EXE],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    env=env,
)
print("  Waiting for service to start…")
time.sleep(3)

if proc.poll() is not None:
    out = proc.stdout.read().decode(errors="replace")
    fail(f"XmlaRunner exited early (code {proc.returncode})\n{out}")
    exit(1)
ok(f"XmlaRunner started (PID {proc.pid})")

# ── Step 3: Health check ───────────────────────────────────────────────────────
step(3, f"Health check  GET {HEALTH_URL}")

try:
    with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
        health = json.loads(r.read())
    if health.get("ok"):
        ok(f"Response: {health}")
    else:
        fail(f"Unexpected response: {health}")
except Exception as e:
    fail(f"Health check failed: {e}")
    proc.terminate()
    exit(1)

# ── Step 4: Run DAX query ──────────────────────────────────────────────────────
step(4, f"POST /run-dax  —  DAX: {TEST_DAX}")

bearer_token = __import__('json').loads(
    __import__('urllib.request', fromlist=['urlopen']).urlopen(
        __import__('urllib.request', fromlist=['Request']).Request(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
            data=__import__('urllib.parse', fromlist=['urlencode']).urlencode({
                "grant_type": "client_credentials", "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "https://analysis.windows.net/powerbi/api/.default",
            }).encode()
        ), timeout=15
    ).read()
)["access_token"]

payload = json.dumps({
    "Workspace":   WORKSPACE,
    "Dataset":     DATASET,
    "Dax":         TEST_DAX,
    "BearerToken": bearer_token,
    "MaxRows":     10,
}).encode()

req = urllib.request.Request(
    XMLA_API,
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=30) as r:
        dax_resp = json.loads(r.read())
    ok("DAX query succeeded!")
    print(f"\n  Columns : {dax_resp.get('columns')}")
    print(f"  Rows    : {dax_resp.get('rows')}")
except urllib.error.HTTPError as e:
    body = e.read().decode(errors="replace")
    fail(f"HTTP {e.code}: {body}")
except Exception as e:
    fail(f"DAX request failed: {e}")
finally:
    proc.terminate()
    print(f"\n  XmlaRunner stopped.")

print(f"\n{SEP}\nDone.\n{SEP}")
