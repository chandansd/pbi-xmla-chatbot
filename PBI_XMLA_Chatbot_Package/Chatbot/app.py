import os, json, subprocess, time, requests, atexit
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────────
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
AZURE_BASE        = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_KEY         = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
MODEL_NAME        = os.getenv("MODEL_NAME", "gpt-4o-mini")

WORKSPACE      = os.getenv("WORKSPACE", "AITests")
DATASET        = os.getenv("DATASET",   "BournoutPBIDashboard")
XMLA_API       = os.getenv("XMLA_API",  "http://localhost:5000/run-dax")
XMLA_HEALTH    = "http://localhost:5000/"

RUNNER_EXE = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "..", "XmlaRunner", "bin", "Debug", "net48", "XmlaRunner.exe"
))

# ── Start XmlaRunner if not already running ──────────────────────────────────
_runner_proc = None

def ensure_runner():
    global _runner_proc
    try:
        requests.get(XMLA_HEALTH, timeout=2)
        return  # already up
    except Exception:
        pass

    _runner_proc = subprocess.Popen(
        [RUNNER_EXE],
        env=os.environ.copy(),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    for _ in range(10):
        time.sleep(1)
        try:
            requests.get(XMLA_HEALTH, timeout=1)
            print(f"[XmlaRunner] Started (PID {_runner_proc.pid})")
            return
        except Exception:
            pass
    raise RuntimeError("XmlaRunner did not start in time.")

def shutdown_runner():
    if _runner_proc:
        _runner_proc.terminate()

atexit.register(shutdown_runner)

# ── OpenAI client ─────────────────────────────────────────────────────────────
if AZURE_BASE and AZURE_KEY:
    client = OpenAI(
        api_key=AZURE_KEY,
        base_url=f"{AZURE_BASE}/openai/deployments",
        default_headers={"api-key": AZURE_KEY},
    )
    extra_args = {"api_version": AZURE_API_VERSION}
else:
    client = OpenAI(api_key=OPENAI_API_KEY)
    extra_args = {}

# ── Schema ────────────────────────────────────────────────────────────────────
SCHEMA = """
=== Power BI Dataset: BournoutPBIDashboard ===

Table: Dim_Employee
  Columns (use EXACTLY these names):
    Dim_Employee[EmployeeKey]          -- integer, primary key
    Dim_Employee[Name]                 -- text
    Dim_Employee[Gender]               -- text  e.g. "Male", "Female"
    Dim_Employee[JobRole]              -- text  e.g. "Sales", "Engineering"
    Dim_Employee[Work Model]           -- text  e.g. "Low Remote", "High Remote"
    Dim_Employee[Satisfaction Category]-- text  e.g. "Medium Satisfcation"
    Dim_Employee[Stress Category]      -- text  e.g. "Medium Stress", "High Stress"
    Dim_Employee[Burnout Status]       -- text  e.g. "Burnout", "No Burnout"
    Dim_Employee[Experience Level]     -- text  e.g. "Mid-Level", "Senior"
    Dim_Employee[Working Hours Category]-- text e.g. "Over 50 Hours"
    Dim_Employee[Age Group]            -- text  e.g. "30-39"

Table: Fact_EmployeeMetrics
  Columns (use EXACTLY these names):
    Fact_EmployeeMetrics[EmployeeKey]      -- integer, foreign key → Dim_Employee
    Fact_EmployeeMetrics[Age]              -- integer
    Fact_EmployeeMetrics[Experience]       -- integer (years)
    Fact_EmployeeMetrics[WorkHoursPerWeek] -- integer
    Fact_EmployeeMetrics[RemoteRatio]      -- integer (0-100)
    Fact_EmployeeMetrics[SatisfactionLevel]-- decimal
    Fact_EmployeeMetrics[StressLevel]      -- integer (higher = more stress)
    Fact_EmployeeMetrics[Burnout]          -- integer (1 = burned out, 0 = not)

Pre-built Measures (reference as [MeasureName]):
  [Total Employees], [Burnout Count], [Burnout Rate],
  [Avg Work Hours], [Avg Stress Level], [Avg Satisfaction Level],
  [Avg Experience], [Avg Burnout], [Avg Remote Ratio],
  [Total Females], [Total Males], [% Female], [% Male],
  [No Burnout], [No Burnout Rate]

DAX Rules — follow STRICTLY:
- ALWAYS use the exact column names listed above. NEVER invent column names.
- Columns with spaces need brackets but no extra quotes: Dim_Employee[Work Model]
- Join between tables is on EmployeeKey (use NATURALINNERJOIN or RELATED in SUMMARIZECOLUMNS)
- Every query MUST start with EVALUATE
- Use VAR / RETURN for multi-step calculations

Pattern for employee detail (cross-table lookup):
  EVALUATE
  VAR Combined = NATURALINNERJOIN(Dim_Employee, Fact_EmployeeMetrics)
  RETURN
  TOPN(1, Combined, Fact_EmployeeMetrics[StressLevel], DESC)

Pattern for group-by aggregation:
  EVALUATE
  SUMMARIZECOLUMNS(
      Dim_Employee[JobRole],
      "Burnout Rate", [Burnout Rate],
      "Avg Stress", [Avg Stress Level]
  )
  ORDER BY [Burnout Rate] DESC
"""

SYSTEM_PROMPT = f"""You are an expert Power BI / DAX analyst assistant.
The user will ask questions about employee burnout data. You MUST always call
the run_dax tool with a valid DAX query to retrieve data before answering.

{SCHEMA}

Guidelines:
- Write clean, correct DAX. The query runs on a real Analysis Services model.
- You CAN write any DAX — ad-hoc measures, complex filters, calculated columns.
- If the question can be answered from the data, always fetch it.
- After you receive the data, write a clear, insightful answer in plain English.
- Highlight key numbers, trends, or anomalies.
- If the user asks a follow-up, use prior context from the conversation.
"""

# ── Tool definition ────────────────────────────────────────────────────────────
TOOL = {
    "type": "function",
    "function": {
        "name": "run_dax",
        "description": "Execute any DAX query against the Power BI dataset via XMLA and return the result.",
        "parameters": {
            "type": "object",
            "properties": {
                "dax": {
                    "type": "string",
                    "description": "A complete, valid DAX query starting with EVALUATE."
                }
            },
            "required": ["dax"]
        }
    }
}

# ── DAX execution ─────────────────────────────────────────────────────────────
def run_dax(dax: str, max_rows: int = 500) -> dict:
    ensure_runner()
    r = requests.post(
        XMLA_API,
        json={"Workspace": WORKSPACE, "Dataset": DATASET, "Dax": dax, "MaxRows": max_rows},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()

# ── FastAPI ────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_runner()
    yield
    shutdown_runner()

app = FastAPI(title="Employee Burnout Chatbot", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

app.mount("/web", StaticFiles(directory="web", html=True), name="web")

class ChatReq(BaseModel):
    message: str
    history: list = []   # [{role, content}]

@app.get("/")
def root():
    return FileResponse("web/index.html")

@app.post("/chat")
def chat(req: ChatReq):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += req.history
    messages.append({"role": "user", "content": req.message})

    dax_used    = None
    data_rows   = []
    row_count   = 0
    dax_error   = None

    # Allow up to 2 DAX attempts (model can self-correct on error)
    for attempt in range(2):
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=[TOOL],
            tool_choice="auto",
            temperature=0.1,
            **extra_args
        )
        msg = resp.choices[0].message

        if not getattr(msg, "tool_calls", None):
            # Model answered directly without querying data
            return {
                "answer":   msg.content,
                "dax":      dax_used,
                "preview":  data_rows[:10],
                "rowCount": row_count,
            }

        tc   = msg.tool_calls[0]
        args = json.loads(tc.function.arguments)
        dax_used = args["dax"]

        tool_calls_dict = [{
            "id": tc.id, "type": "function",
            "function": {"name": tc.function.name, "arguments": tc.function.arguments}
        }]
        messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls_dict})

        try:
            result    = run_dax(dax_used)
            data_rows = result.get("rows", [])
            row_count = len(data_rows)
            dax_error = None
            tool_content = json.dumps({
                "status":         "success",
                "rowCount":       row_count,
                "result_preview": data_rows[:20],
            })
        except Exception as e:
            dax_error    = str(e)
            tool_content = json.dumps({"status": "error", "error": dax_error})

        if dax_error:
            tool_content = json.dumps({
                "status": "error",
                "error":  dax_error,
                "instruction": (
                    "The DAX query failed. Fix it using ONLY the exact column names "
                    "from the schema in the system prompt. "
                    "For cross-table employee detail use: "
                    "NATURALINNERJOIN(Dim_Employee, Fact_EmployeeMetrics). "
                    "Do NOT use EmployeeID, Age, Department, or any column not listed."
                )
            })

        messages.append({
            "role": "tool", "tool_call_id": tc.id,
            "name": "run_dax", "content": tool_content
        })

        if dax_error is None:
            break  # success — move on to final answer

    # Final turn: write natural-language answer from data
    final = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=0.3,
        **extra_args
    )

    return {
        "answer":   final.choices[0].message.content,
        "dax":      dax_used,
        "preview":  data_rows[:10],
        "rowCount": row_count,
        "error":    dax_error,
    }
