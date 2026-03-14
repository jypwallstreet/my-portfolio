from flask import Flask, render_template_string, jsonify, request
import yfinance as yf
from datetime import datetime
import anthropic
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")

app    = Flask(__name__)
claude = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

portfolio = {
    "SK Hynix":     {"ticker": "000660.KS", "shares": 1,  "avg_cost": 951000,  "currency": "KRW"},
    "Agnico Eagle": {"ticker": "AEM",        "shares": 2,  "avg_cost": 228.75,  "currency": "USD"},
    "Broadcom":     {"ticker": "AVGO",       "shares": 2,  "avg_cost": 342.655, "currency": "USD"},
    "NVIDIA":       {"ticker": "NVDA",       "shares": 3,  "avg_cost": 196.40,  "currency": "USD"},
}

_report_cache = {"html": None, "ts": None}


# ═══════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

def get_verdict(pct):
    if pct > 2:      return "🚀 Strong Up",   "up"
    elif pct > 0.5:  return "📈 Up",           "up"
    elif pct < -2:   return "🔴 Strong Down",  "down"
    elif pct < -0.5: return "📉 Down",         "down"
    else:            return "➡️ Flat",          "flat"

def get_sentiment(name, headlines):
    if not headlines:
        return "neutral", "⚪ Neutral", ""
    try:
        hl  = "\n".join(f"- {h}" for h in headlines)
        msg = claude.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=100,
            messages=[{"role": "user", "content":
                f"Analyze news for {name}. Reply ONLY:\nSENTIMENT: bullish/bearish/neutral\nSUMMARY: one sentence max 15 words\n\nHeadlines:\n{hl}"}]
        )
        text = msg.content[0].text.strip()
        sentiment, summary = "neutral", ""
        for line in text.split("\n"):
            if line.startswith("SENTIMENT:"):
                sentiment = line.replace("SENTIMENT:", "").strip().lower()
            elif line.startswith("SUMMARY:"):
                summary = line.replace("SUMMARY:", "").strip()
        lbl = {"bullish": "🟢 Bullish", "bearish": "🔴 Bearish"}.get(sentiment, "🟡 Neutral")
        cls = {"bullish": "bullish",    "bearish": "bearish"   }.get(sentiment, "neutral")
        return cls, lbl, summary
    except:
        return "neutral", "🟡 Neutral", ""

def get_chart_data(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="3mo")
        if hist.empty:
            return {"dates": [], "prices": []}
        return {
            "dates":  [d.strftime("%b %d") for d in hist.index],
            "prices": [round(float(p), 2) for p in hist["Close"]]
        }
    except:
        return {"dates": [], "prices": []}

def fetch_stock(name, info):
    ticker, shares, avg_cost = info["ticker"], info["shares"], info["avg_cost"]
    sym = "₩" if info["currency"] == "KRW" else "$"
    try:
        s    = yf.Ticker(ticker)
        data = s.history(period="5d")
        meta = s.info
        if data.empty or len(data) < 2:
            return None
        prev = float(data["Close"].iloc[-2])
        curr = float(data["Close"].iloc[-1])
        chg  = curr - prev
        pct  = (chg / prev) * 100
        verdict, vcls = get_verdict(pct)
        day_pnl   = chg * shares
        total_pnl = (curr - avg_cost) * shares
        total_pct = (total_pnl / (avg_cost * shares)) * 100
        h52 = meta.get("fiftyTwoWeekHigh")
        l52 = meta.get("fiftyTwoWeekLow")
        range_str  = f"{sym}{l52:.2f} – {sym}{h52:.2f}" if h52 and l52 else "N/A"
        rating     = meta.get("recommendationKey", "N/A").upper()
        target     = meta.get("targetMeanPrice")
        target_str = f"${target:.2f}" if target else "N/A"
        headlines  = []
        try:
            for a in s.news[:3]:
                t = (a.get("content") or {}).get("title") or a.get("title")
                if t: headlines.append(t)
        except: pass
        scls, slbl, ssum = get_sentiment(name, headlines)
        chart = get_chart_data(ticker)
        return {
            "name": name, "ticker": ticker, "symbol": sym,
            "shares": shares, "avg_cost": avg_cost,
            "price": curr, "change": chg, "percent": pct,
            "day_pnl": day_pnl, "total_pnl": total_pnl, "total_pct": total_pct,
            "range_str": range_str, "rating": rating, "target_str": target_str,
            "verdict": verdict, "verdict_class": vcls, "news": headlines,
            "sentiment_class": scls, "sentiment_label": slbl, "sentiment_summary": ssum,
            "chart": chart,
        }
    except Exception as e:
        print(f"Error {ticker}: {e}")
        return None

def generate_report():
    lines = []
    for name, info in portfolio.items():
        sym = "₩" if info["currency"] == "KRW" else "$"
        lines.append(f"- {name} ({info['ticker']}): {info['shares']} shares @ {sym}{info['avg_cost']}")
    ptxt = "\n".join(lines)
    try:
        msg = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": f"""You are a senior equity analyst at Goldman Sachs. Write a full professional research report for this portfolio:

{ptxt}

Output clean HTML only (no markdown, no backticks). Use this exact structure:

<h1>Equity Research Report — {datetime.now().strftime("%B %d, %Y")}</h1>
<p style="color:#8b949e">Prepared by AI Research Desk</p>

<h2>Executive Summary</h2>
[HTML table with columns: Stock | Current Price | P/E vs Sector | Moat | Risk /10 | Bull Target | Bear Target | Rating]

<h2>Individual Stock Analysis</h2>
[For each stock use <h3>Name (TICKER)</h3> then bullet points covering:
• P/E ratio vs sector average — overvalued or undervalued?
• Revenue growth over last 5 years — actual numbers
• Debt-to-equity ratio — healthy for this sector?
• Dividend yield and payout sustainability (Low/Medium/High)
• Competitive moat (Weak/Moderate/Strong) + 2 sentence reasoning
• 12-month Bull case price target + reasoning
• 12-month Bear case price target + reasoning
• Risk rating X/10 with bullet-point reasoning
• Entry price zone and stop-loss recommendation]

<h2>Global Market News & Analysis</h2>
[Search for latest major news RIGHT NOW. Cover everything: macro trends, Fed policy, geopolitical risks, earnings, sector news, tariffs, inflation, AI trends, commodity prices — anything that could impact these stocks. Be thorough and specific. Don't leave anything out.]

<h2>Near-Term Outlook (3–6 Months)</h2>
[Based on all research and news: what is LIKELY to happen to these stocks and the broader market? Be direct, specific, and honest. No vague language.]

<h2>⭐ Top Pick Recommendation</h2>
[Based on everything above — fundamentals, news, risk/reward, market conditions — recommend the ONE best stock to own right now. Can be from this portfolio or a new one to add. Give full conviction reasoning.]

Use <span style="color:#3fb950"> for bullish/positive and <span style="color:#f85149"> for bearish/negative text. Style tables with border-collapse:collapse, add padding to cells. Make it look and read like a real Goldman Sachs research report."""}]
        )
        return "".join(b.text for b in msg.content if hasattr(b, "text"))
    except Exception as e:
        return f"<p style='color:#f85149'>Error generating report: {e}</p>"


# ═══════════════════════════════════════════════════════════
#  HTML TEMPLATES
# ═══════════════════════════════════════════════════════════

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Portfolio Dashboard</title>
    <meta charset="UTF-8">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:30px}
        h1{font-size:24px;color:#58a6ff;margin-bottom:4px}
        .ts{color:#8b949e;font-size:13px;margin-bottom:24px}
        .topbar{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px;flex-wrap:wrap;gap:16px}
        .summary{display:flex;gap:14px;flex-wrap:wrap}
        .scard{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px 24px;min-width:170px}
        .scard .lbl{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
        .scard .val{font-size:22px;font-weight:600}
        .btns{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
        .btn{padding:10px 18px;border-radius:8px;border:none;cursor:pointer;font-size:14px;font-weight:500;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
        .btn-blue{background:#1f6feb;color:white}.btn-blue:hover{background:#388bfd}
        .btn-green{background:#238636;color:white}.btn-green:hover{background:#2ea043}
        .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px}
        .card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px}
        .card-hdr{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px}
        .sname{font-size:16px;font-weight:600}
        .tkr{font-size:12px;color:#8b949e;margin-top:2px}
        .badges{display:flex;gap:5px;flex-direction:column;align-items:flex-end}
        .badge{font-size:12px;font-weight:500;padding:3px 10px;border-radius:20px}
        .up{background:#1a3a2a;color:#3fb950}.down{background:#3a1a1a;color:#f85149}.flat{background:#2a2a1a;color:#d29922}
        .bullish{background:#1a3a2a;color:#3fb950}.bearish{background:#3a1a1a;color:#f85149}.neutral{background:#2a2a1a;color:#d29922}
        .row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #21262d;font-size:14px}
        .row:last-child{border-bottom:none}
        .row .l{color:#8b949e}
        .green{color:#3fb950}.red{color:#f85149}
        .chart-wrap{margin-top:12px;height:75px}
        .news{margin-top:12px;padding-top:12px;border-top:1px solid #21262d}
        .nlbl{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
        .ni{font-size:12px;color:#c9d1d9;padding:3px 0;line-height:1.4}
        .ssum{font-size:12px;color:#8b949e;margin-top:6px;font-style:italic}
        #chat-btn{position:fixed;bottom:28px;right:28px;width:54px;height:54px;border-radius:50%;background:#1f6feb;border:none;cursor:pointer;font-size:22px;color:white;box-shadow:0 4px 14px rgba(0,0,0,.5);z-index:1000}
        #chat-panel{position:fixed;bottom:96px;right:28px;width:360px;background:#161b22;border:1px solid #30363d;border-radius:12px;display:none;flex-direction:column;box-shadow:0 8px 28px rgba(0,0,0,.6);z-index:1000;overflow:hidden}
        #chat-panel.open{display:flex}
        .chdr{padding:13px 18px;background:#1f6feb;font-weight:600;font-size:14px;display:flex;justify-content:space-between;align-items:center}
        .cclose{cursor:pointer;background:none;border:none;color:white;font-size:18px}
        #chat-msgs{height:280px;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
        .msg{font-size:13px;line-height:1.5;max-width:92%;padding:9px 13px;border-radius:10px}
        .msg.user{background:#1f6feb;color:white;align-self:flex-end}
        .msg.ai{background:#21262d;color:#e6edf3;align-self:flex-start}
        .cinput-row{display:flex;padding:11px;gap:8px;border-top:1px solid #21262d}
        #cinput{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:9px;color:#e6edf3;font-size:13px;outline:none}
        #csend{background:#1f6feb;border:none;color:white;padding:9px 13px;border-radius:8px;cursor:pointer;font-size:13px}
        #csend:hover{background:#388bfd}
    </style>
</head>
<body>
<h1>📊 Portfolio Dashboard</h1>
<div class="ts">Last updated: {{ timestamp }}</div>

<div class="topbar">
    <div class="summary">
        <div class="scard">
            <div class="lbl">Today's P&L (USD)</div>
            <div class="val {{ 'green' if total_day >= 0 else 'red' }}">{{ '+' if total_day >= 0 else '' }}${{ "%.2f"|format(total_day) }}</div>
        </div>
        <div class="scard">
            <div class="lbl">All-time P&L (USD)</div>
            <div class="val {{ 'green' if total_alltime >= 0 else 'red' }}">{{ '+' if total_alltime >= 0 else '' }}${{ "%.2f"|format(total_alltime) }}</div>
        </div>
    </div>
    <div class="btns">
        <a href="/report" class="btn btn-blue">📋 Analyst Report</a>
        <button class="btn btn-green" id="email-btn" onclick="sendEmail()">📧 Email Digest</button>
    </div>
</div>

<div class="grid">
{% for stock in stocks %}
<div class="card">
    <div class="card-hdr">
        <div>
            <div class="sname">{{ stock.name }}</div>
            <div class="tkr">{{ stock.ticker }}</div>
        </div>
        <div class="badges">
            <div class="badge {{ stock.verdict_class }}">{{ stock.verdict }}</div>
            <div class="badge {{ stock.sentiment_class }}">{{ stock.sentiment_label }}</div>
        </div>
    </div>
    <div class="row"><span class="l">Price</span><span>{{ stock.symbol }}{{ "%.2f"|format(stock.price) }}</span></div>
    <div class="row">
        <span class="l">Change</span>
        <span class="{{ 'green' if stock.change >= 0 else 'red' }}">{{ '+' if stock.change >= 0 else '' }}{{ "%.2f"|format(stock.change) }} ({{ '+' if stock.percent >= 0 else '' }}{{ "%.2f"|format(stock.percent) }}%)</span>
    </div>
    <div class="row"><span class="l">52W Range</span><span>{{ stock.range_str }}</span></div>
    <div class="row"><span class="l">Analyst</span><span>{{ stock.rating }} | {{ stock.target_str }}</span></div>
    <div class="row"><span class="l">Shares</span><span>{{ stock.shares }} @ {{ stock.symbol }}{{ stock.avg_cost }}</span></div>
    <div class="row">
        <span class="l">Today P&L</span>
        <span class="{{ 'green' if stock.day_pnl >= 0 else 'red' }}">{{ '+' if stock.day_pnl >= 0 else '' }}{{ "%.2f"|format(stock.day_pnl) }}</span>
    </div>
    <div class="row">
        <span class="l">Total P&L</span>
        <span class="{{ 'green' if stock.total_pnl >= 0 else 'red' }}">{{ '+' if stock.total_pnl >= 0 else '' }}{{ "%.2f"|format(stock.total_pnl) }} ({{ '+' if stock.total_pct >= 0 else '' }}{{ "%.2f"|format(stock.total_pct) }}%)</span>
    </div>
    <div class="chart-wrap"><canvas id="c{{ loop.index }}"></canvas></div>
    {% if stock.news %}
    <div class="news">
        <div class="nlbl">Latest News</div>
        {% for item in stock.news %}<div class="ni">• {{ item }}</div>{% endfor %}
        {% if stock.sentiment_summary %}<div class="ssum">🤖 {{ stock.sentiment_summary }}</div>{% endif %}
    </div>
    {% endif %}
</div>
{% endfor %}
</div>

<button id="chat-btn" onclick="toggleChat()">💬</button>
<div id="chat-panel">
    <div class="chdr">
        <span>🤖 Portfolio AI Advisor</span>
        <button class="cclose" onclick="toggleChat()">✕</button>
    </div>
    <div id="chat-msgs">
        <div class="msg ai">Ask me anything about your portfolio — what to buy, sell, hold, risks, strategy, anything.</div>
    </div>
    <div class="cinput-row">
        <input id="cinput" placeholder="Ask about your portfolio..." />
        <button id="csend">Send</button>
    </div>
</div>

<script>
const chartData = {{ chart_data | tojson }};
const chatHistory = [];

chartData.forEach((d, i) => {
    const ctx = document.getElementById('c' + (i+1));
    if (!ctx || !d.prices.length) return;
    const isUp = d.prices[d.prices.length-1] >= d.prices[0];
    const color = isUp ? '#3fb950' : '#f85149';
    new Chart(ctx, {
        type: 'line',
        data: {
            labels: d.dates,
            datasets: [{
                data: d.prices, borderColor: color, borderWidth: 1.5,
                fill: true, backgroundColor: isUp ? 'rgba(63,185,80,0.07)' : 'rgba(248,81,73,0.07)',
                pointRadius: 0, tension: 0.3
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: { x: { display: false }, y: { display: false } }
        }
    });
});

function toggleChat() {
    document.getElementById('chat-panel').classList.toggle('open');
}

document.getElementById('csend').addEventListener('click', sendMsg);
document.getElementById('cinput').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') sendMsg();
});

async function sendMsg() {
    const input = document.getElementById('cinput');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMsg(text, 'user');
    chatHistory.push({ role: 'user', content: text });
    const thinking = addMsg('Thinking...', 'ai', true);
    const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, history: chatHistory.slice(-6) })
    });
    const data = await res.json();
    thinking.remove();
    addMsg(data.reply, 'ai');
    chatHistory.push({ role: 'assistant', content: data.reply });
}

function addMsg(text, role, temp) {
    const c = document.getElementById('chat-msgs');
    const d = document.createElement('div');
    d.className = 'msg ' + role;
    d.textContent = text;
    c.appendChild(d);
    c.scrollTop = c.scrollHeight;
    return d;
}

async function sendEmail() {
    const btn = document.getElementById('email-btn');
    btn.textContent = '⏳ Sending...';
    btn.disabled = true;
    const res = await fetch('/send-email', { method: 'POST' });
    const data = await res.json();
    btn.textContent = data.success ? '✅ Sent!' : '❌ Failed — check config';
    setTimeout(() => { btn.textContent = '📧 Email Digest'; btn.disabled = false; }, 3000);
}
</script>
</body>
</html>
"""

REPORT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Analyst Report</title>
    <meta charset="UTF-8">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:40px;max-width:1100px;margin:0 auto}
        .back{display:inline-block;margin-bottom:24px;padding:8px 16px;background:#21262d;border-radius:6px;color:#e6edf3;text-decoration:none;font-size:14px}
        .back:hover{background:#30363d}
        #report h1{color:#58a6ff;font-size:26px;margin:24px 0 10px}
        #report h2{color:#58a6ff;font-size:19px;margin:28px 0 10px;border-bottom:1px solid #21262d;padding-bottom:8px}
        #report h3{color:#e6edf3;font-size:16px;margin:20px 0 8px}
        #report p{color:#c9d1d9;line-height:1.7;margin:8px 0}
        #report ul,#report ol{color:#c9d1d9;margin:8px 0 8px 20px;line-height:1.8}
        #report table{width:100%;border-collapse:collapse;margin:14px 0;font-size:14px}
        #report th{background:#21262d;padding:10px 14px;text-align:left;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
        #report td{padding:10px 14px;border-bottom:1px solid #21262d}
        #report tr:hover td{background:#161b22}
        #report strong{color:#e6edf3}
        .loading{display:flex;flex-direction:column;align-items:center;justify-content:center;height:65vh;gap:18px}
        .spinner{width:46px;height:46px;border:4px solid #30363d;border-top-color:#58a6ff;border-radius:50%;animation:spin 1s linear infinite}
        @keyframes spin{to{transform:rotate(360deg)}}
        .ltxt{color:#8b949e;font-size:15px;text-align:center;max-width:340px;line-height:1.6}
        .gen-btn{margin-bottom:20px;padding:10px 20px;background:#1f6feb;color:white;border:none;border-radius:8px;font-size:14px;cursor:pointer}
        .gen-btn:hover{background:#388bfd}
    </style>
</head>
<body>
<a href="/" class="back">← Dashboard</a>
<button class="gen-btn" id="gen-btn" onclick="generateReport()">🔄 Regenerate Report</button>

<div id="loading" class="loading">
    <div class="spinner"></div>
    <div class="ltxt">Generating your analyst report...<br>Claude is searching the web for current data.<br><br>This takes about 30–60 seconds.</div>
</div>
<div id="report" style="display:none"></div>

<script>
let loaded = false;

async function loadReport() {
    if (loaded) return;
    const res = await fetch('/report-data');
    const data = await res.json();
    document.getElementById('loading').style.display = 'none';
    document.getElementById('report').style.display = 'block';
    document.getElementById('report').innerHTML = data.html;
    loaded = true;
}

async function generateReport() {
    loaded = false;
    document.getElementById('loading').style.display = 'flex';
    document.getElementById('report').style.display = 'none';
    document.getElementById('gen-btn').textContent = '⏳ Generating...';
    document.getElementById('gen-btn').disabled = true;
    const res = await fetch('/report-data?refresh=1');
    const data = await res.json();
    document.getElementById('loading').style.display = 'none';
    document.getElementById('report').style.display = 'block';
    document.getElementById('report').innerHTML = data.html;
    document.getElementById('gen-btn').textContent = '🔄 Regenerate Report';
    document.getElementById('gen-btn').disabled = false;
    loaded = true;
}

loadReport();
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════

@app.route("/")
def home():
    stocks = []
    chart_data = []
    total_day = total_alltime = 0
    for name, info in portfolio.items():
        s = fetch_stock(name, info)
        if s:
            stocks.append(s)
            chart_data.append(s["chart"])
            if info["currency"] == "USD":
                total_day     += s["day_pnl"]
                total_alltime += s["total_pnl"]
    return render_template_string(DASHBOARD_HTML,
        stocks=stocks, chart_data=chart_data,
        total_day=total_day, total_alltime=total_alltime,
        timestamp=datetime.now().strftime("%b %d %Y  %H:%M:%S"))

@app.route("/report")
def report_page():
    return render_template_string(REPORT_HTML)

@app.route("/report-data")
def report_data():
    refresh = request.args.get("refresh", "0")
    now = datetime.now()
    if (refresh == "0"
            and _report_cache["html"]
            and _report_cache["ts"]
            and (now - _report_cache["ts"]).seconds < 3600):
        return jsonify({"html": _report_cache["html"]})
    html = generate_report()
    _report_cache["html"] = html
    _report_cache["ts"]   = now
    return jsonify({"html": html})

@app.route("/chat", methods=["POST"])
def chat():
    data    = request.json
    message = data.get("message", "")
    history = data.get("history", [])
    context = "You are a sharp, direct portfolio advisor. User's portfolio:\n"
    for name, info in portfolio.items():
        sym = "₩" if info["currency"]=="KRW" else "$"
        context += f"- {name} ({info['ticker']}): {info['shares']} shares @ {sym}{info['avg_cost']}\n"
    context += "\nBe concise, direct, honest. Max 4 sentences unless more is needed."
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": message})
    try:
        res = claude.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=400,
            system=context, messages=messages
        )
        return jsonify({"reply": res.content[0].text})
    except Exception as e:
        return jsonify({"reply": f"Error: {e}"})

@app.route("/send-email", methods=["POST"])
def send_email():
    try:
        rows = ["Portfolio Daily Digest", "=" * 48, ""]
        total_day = total_alltime = 0
        for name, info in portfolio.items():
            s    = yf.Ticker(info["ticker"])
            data = s.history(period="2d")
            if data.empty or len(data) < 2:
                continue
            prev = float(data["Close"].iloc[-2])
            curr = float(data["Close"].iloc[-1])
            chg  = curr - prev
            pct  = (chg / prev) * 100
            pnl  = chg * info["shares"]
            tpnl = (curr - info["avg_cost"]) * info["shares"]
            sym  = "₩" if info["currency"]=="KRW" else "$"
            if info["currency"] == "USD":
                total_day     += pnl
                total_alltime += tpnl
            rows += [
                f"{name} ({info['ticker']})",
                f"  Price: {sym}{curr:.2f}   Change: {chg:+.2f} ({pct:+.2f}%)",
                f"  Today P&L: {pnl:+.2f}   Total P&L: {tpnl:+.2f}",
                ""
            ]
        rows += ["─" * 48,
                 f"Total Today P&L (USD):    ${total_day:+.2f}",
                 f"Total All-time P&L (USD): ${total_alltime:+.2f}"]
        body = "\n".join(rows)
        msg  = MIMEMultipart()
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = EMAIL_RECEIVER
        msg["Subject"] = f"📊 Portfolio Digest — {datetime.now().strftime('%b %d %Y')}"
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(EMAIL_SENDER, EMAIL_PASSWORD)
            srv.send_message(msg)
        return jsonify({"success": True})
    except Exception as e:
        print(f"Email error: {e}")
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))