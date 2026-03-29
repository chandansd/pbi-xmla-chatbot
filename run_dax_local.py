import os, sys
# Prepend the 'bin' folder so .NET DLLs can be found
os.environ["PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "bin")) + ";" + os.environ["PATH"]
from pyadomd import Pyadomd
import pandas as pd
import json
import os
import sys

def run_dax(query: str):
    conn_str = os.environ["PBI_CONN_STR"]
    with Pyadomd(conn_str) as conn:
        with conn.cursor().execute(query) as cur:
            cols = [c.name for c in cur.description]
            rows = cur.fetchall()
            df = pd.DataFrame(rows, columns=cols)
            return df.to_dict(orient="records")

if __name__ == "__main__":
    dax_query = sys.argv[1]
    try:
        result = run_dax(dax_query)
        print(json.dumps({"ok": True, "results": result}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
