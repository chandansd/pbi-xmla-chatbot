# Power BI XMLA Chatbot

A natural-language chatbot that queries a live Power BI dataset via the XMLA endpoint using GPT-4 function calling. Ask questions in plain English — the model generates DAX, runs it against your dataset, and returns an insightful answer.

> Built for datasets where **Microsoft Copilot is unavailable** (on-premises SSAS Tabular, Azure Analysis Services, or Power BI workspaces without Fabric/Premium Per User).

---

## Architecture

```
Browser  →  FastAPI (Python)  →  XmlaRunner.exe (.NET 4.8)  →  Power BI XMLA Endpoint
                  ↓
           OpenAI / Azure OpenAI
```

- **Chatbot/** — FastAPI backend + single-page HTML frontend
- **XmlaRunner/** — Lightweight C# HTTP service that executes DAX via `AdomdClient`

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | |
| .NET Framework 4.8 | Windows only (required by AdomdClient) |
| Power BI Premium / PPU workspace | XMLA endpoint must be enabled |
| Azure AD App Registration | With `Dataset.ReadWrite.All` permission on Power BI |
| OpenAI API key **or** Azure OpenAI deployment | |

---

## Setup

### 1. Configure environment

```bash
cd Chatbot
cp .env.example .env
# Fill in your keys in .env
```

### 2. Install Python dependencies

```bash
cd Chatbot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Build XmlaRunner

```bash
cd XmlaRunner
dotnet build -c Release
```

### 4. Run

```bash
cd Chatbot
uvicorn app:app --reload --port 8000
```

Open `http://localhost:8000` in your browser.

---

## How It Works

1. User sends a question via the chat UI
2. FastAPI passes it to GPT-4 with a `run_dax` tool and the dataset schema
3. GPT-4 generates a DAX query and calls the tool
4. FastAPI forwards the DAX to XmlaRunner (local .NET process)
5. XmlaRunner executes it against the Power BI XMLA endpoint and returns rows
6. GPT-4 receives the data and writes a natural-language answer
7. The UI displays the answer, a data preview table, and a collapsible DAX block

If the DAX query fails, the model self-corrects and retries once automatically.

---

## Azure AD Setup

1. Go to **Azure Portal → App Registrations → New registration**
2. Add API permission: `Power BI Service → Dataset.ReadWrite.All` (Application type)
3. Grant admin consent
4. Create a client secret under **Certificates & secrets**
5. In Power BI Admin Portal, enable **"Allow service principals to use Power BI APIs"**
6. Add the service principal to your workspace as a **Member or Admin**

---

## Extending to SSAS / Azure Analysis Services

Change the connection string in `XmlaRunner/Program.cs`:

```csharp
// On-premises SSAS Tabular
var cnStr = $"Data Source=YOUR_SERVER\\INSTANCE;Initial Catalog={runReq.Dataset};Integrated Security=SSPI;";

// Azure Analysis Services
var cnStr = $"Data Source=asazure://eastus.asazure.windows.net/YOUR_SERVER;Initial Catalog={runReq.Dataset};User ID=app:{ClientId}@{TenantId};Password={ClientSecret};";
```

No other changes required — DAX and the Python layer remain the same.

---

## Tech Stack

- **FastAPI** + **Uvicorn**
- **OpenAI Python SDK** (compatible with Azure OpenAI)
- **Microsoft.AnalysisServices.AdomdClient** (NuGet)
- Vanilla HTML/CSS/JS frontend (no build step)
