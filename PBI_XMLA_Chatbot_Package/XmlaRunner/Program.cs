using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using Microsoft.AnalysisServices.AdomdClient;
using Newtonsoft.Json;

class Program
{
    static readonly string TenantId     = Environment.GetEnvironmentVariable("TENANT_ID")     ?? "";
    static readonly string ClientId     = Environment.GetEnvironmentVariable("CLIENT_ID")     ?? "";
    static readonly string ClientSecret = Environment.GetEnvironmentVariable("CLIENT_SECRET") ?? "";

    static void Main()
    {
        var listener = new HttpListener();
        listener.Prefixes.Add("http://localhost:5000/");
        listener.Start();
        Console.WriteLine("[XmlaRunner] Listening on http://localhost:5000/");

        while (true)
        {
            var ctx = listener.GetContext();
            System.Threading.ThreadPool.QueueUserWorkItem(_ => Handle(ctx));
        }
    }

    static void Handle(HttpListenerContext ctx)
    {
        var req = ctx.Request;
        var res = ctx.Response;

        try
        {
            if (req.HttpMethod == "GET" && req.Url.AbsolutePath == "/")
            {
                Respond(res, 200, new { ok = true, service = "XmlaRunner" });
                return;
            }

            if (req.HttpMethod == "POST" && req.Url.AbsolutePath == "/run-dax")
            {
                string body = new StreamReader(req.InputStream, Encoding.UTF8).ReadToEnd();
                var runReq  = JsonConvert.DeserializeObject<RunReq>(body);

                if (runReq == null || string.IsNullOrWhiteSpace(runReq.Workspace) ||
                    string.IsNullOrWhiteSpace(runReq.Dataset) || string.IsNullOrWhiteSpace(runReq.Dax))
                {
                    Respond(res, 400, new { error = "Workspace, Dataset and Dax are required." });
                    return;
                }

                // Connection string using service principal
                var cnStr = $"Data Source=powerbi://api.powerbi.com/v1.0/myorg/{runReq.Workspace};" +
                            $"Initial Catalog={runReq.Dataset};" +
                            $"User ID=app:{ClientId}@{TenantId};" +
                            $"Password={ClientSecret};";

                using (var cn = new AdomdConnection(cnStr))
                {
                    cn.Open();
                    using (var cmd = cn.CreateCommand())
                    {
                        cmd.CommandText = runReq.Dax;
                        using (var rd = cmd.ExecuteReader())
                        {
                            var cols = new List<string>();
                            for (int i = 0; i < rd.FieldCount; i++)
                                cols.Add(rd.GetName(i));

                            var rows    = new List<Dictionary<string, object>>();
                            int maxRows = runReq.MaxRows > 0 ? runReq.MaxRows : 1000;

                            while (rd.Read() && rows.Count < maxRows)
                            {
                                var row = new Dictionary<string, object>();
                                for (int i = 0; i < rd.FieldCount; i++)
                                    row[cols[i]] = rd.IsDBNull(i) ? null : rd.GetValue(i);
                                rows.Add(row);
                            }

                            Respond(res, 200, new { columns = cols, rows });
                        }
                    }
                }
                return;
            }

            Respond(res, 404, new { error = "Not found" });
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[ERROR] {ex.Message}");
            try { Respond(res, 500, new { error = ex.Message, detail = ex.InnerException?.Message }); }
            catch { }
        }
    }

    static void Respond(HttpListenerResponse res, int statusCode, object body)
    {
        res.StatusCode   = statusCode;
        res.ContentType  = "application/json";
        var json  = JsonConvert.SerializeObject(body);
        var bytes = Encoding.UTF8.GetBytes(json);
        res.ContentLength64 = bytes.Length;
        res.OutputStream.Write(bytes, 0, bytes.Length);
        res.OutputStream.Close();
    }
}

class RunReq
{
    public string Workspace { get; set; }
    public string Dataset   { get; set; }
    public string Dax       { get; set; }
    public int    MaxRows   { get; set; }
}
