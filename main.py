from fastapi import FastAPI, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import feedparser
import google.generativeai as genai
from supabase import create_client
import pdfplumber, docx2txt, io, os
from typing import Optional

# ✅ 1. إنشاء app أولاً
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ 2. إعداد Gemini و Supabase
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# ✅ 3. RSS Sources
RSS_SOURCES = {
    "Argaam": "https://www.argaam.com/ar/rss/ho-main-news?sectionid=1523",
    "CNN": "https://arabic.cnn.com/api/v1/rss/rss.xml"
}

# ─────────────────────────────────────────
# RSS News
# ─────────────────────────────────────────
@app.get("/news")
def get_news(source: str = "Argaam"):
    url = RSS_SOURCES.get(source)
    if not url:
        return {"error": "Source not found"}
    feed = feedparser.parse(url)
    news_list = []
    for entry in feed.entries[:10]:
        news_list.append({
            "title": entry.title,
            "link": entry.link,
            "published": entry.get("published", "N/A"),
            "summary": entry.summary[:150] + "..." if "summary" in entry else ""
        })
    return {"source": source, "articles": news_list}


# ─────────────────────────────────────────
# Document Analysis
# ─────────────────────────────────────────
async def extract_text(file: UploadFile) -> str:
    content = await file.read()
    name = file.filename.lower()
    if name.endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(content))
        return df.to_string()
    elif name.endswith(".docx"):
        return docx2txt.process(io.BytesIO(content))
    return ""

@app.post("/upload/analyze")
async def analyze_and_store(
    file: UploadFile = File(...),
    ticker: Optional[str] = Form(None),
    category: Optional[str] = Form("عام"),
):
    text = await extract_text(file)
    if not text.strip():
        return {"error": "لم يتم استخراج نص من الملف"}

    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(f"""
    أنت محلل مالي متخصص في السوق السعودي.
    حلل هذا المستند واستخرج:
    1. 📊 أبرز الأرقام المالية
    2. 📈 المقارنة بالفترة السابقة
    3. 💡 ملخص تنفيذي في 3 جمل
    4. ⚡ التأثير المتوقع على السهم
    5. ⚠️ مخاطر تستحق الانتباه
    النص: {text[:4000]}
    """)

    sb.table("documents").insert({
        "filename": file.filename,
        "ticker":   ticker,
        "category": category,
        "raw_text": text[:10000],
        "analysis": response.text,
    }).execute()

    return {"filename": file.filename, "analysis": response.text}

@app.get("/documents")
def get_documents(ticker: str = None):
    query = sb.table("documents")\
        .select("id, filename, ticker, category, analysis, created_at")\
        .order("created_at", desc=True)
    if ticker:
        query = query.eq("ticker", ticker)
    return {"data": query.execute().data}


# ─────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────
def safe_float(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)
    except:
        return default

def calculate_fair_value(info):
    try:
        eps = safe_float(info.get('trailingEps', 0))
        growth = safe_float(info.get('earningsQuarterlyGrowth', 0.05))
        fair_val = eps * (8.5 + 2 * (growth * 100))
        return round(fair_val, 2) if fair_val > 0 else 0
    except:
        return 0

def calculate_metrics(income, balance, cashflow, info):
    res = {}
    try:
        rev    = safe_float(income.get('Total Revenue', 0))
        ni     = safe_float(income.get('Net Income', 0))
        ebit   = safe_float(income.get('Operating Income', income.get('EBIT', 0)))
        ebitda = safe_float(income.get('EBITDA', info.get('ebitda', ebit)))
        equity = safe_float(balance.get('Stockholders Equity', balance.get('Total Equity Gross Minority Interest', 0)))
        total_debt = safe_float(balance.get('Total Debt', 0))
        net_debt   = safe_float(info.get('netDebt', total_debt - safe_float(balance.get('Cash And Cash Equivalents', 0))))
        op_cash    = safe_float(cashflow.get('Operating Cash Flow', 0))
        capex      = abs(safe_float(cashflow.get('Capital Expenditure', 0)))
        fcf        = op_cash - capex
        interest_expense = abs(safe_float(income.get('Interest Expense', 0)))
        working_cap = safe_float(balance.get('Current Assets', 0)) - safe_float(balance.get('Current Liabilities', 0))

        res["roic"]            = round((ebit / (equity + total_debt)) * 100, 2) if (equity + total_debt) > 0 else 0
        res["roe"]             = round((ni / equity) * 100, 2) if equity > 0 else 0
        res["roe_roic_ratio"]  = round(res["roe"] / res["roic"], 2) if res.get("roic", 0) > 0 else 0
        res["ebit_margin"]     = round((ebit / rev) * 100, 2) if rev > 0 else 0
        res["net_debt_ebitda"] = round(net_debt / ebitda, 2) if ebitda > 0 else 0
        res["interest_coverage"] = round(ebit / interest_expense, 2) if interest_expense > 0 else 0
        res["d_e"]             = round(total_debt / equity, 2) if equity > 0 else 0
        res["fcf_yield"]       = round((fcf / safe_float(info.get('marketCap', 1))) * 100, 2) if info.get('marketCap') else 0

        beta      = safe_float(info.get('beta', 1.0), 1.0)
        cost_eq   = 0.045 + (beta * 0.055)
        cost_d    = (interest_expense / total_debt) if total_debt > 0 else 0.045
        mcap      = safe_float(info.get('marketCap', 0))
        total_cap = mcap + total_debt
        wacc_val  = ((mcap / total_cap) * cost_eq) + ((total_debt / total_cap) * cost_d * 0.8) if total_cap > 0 else 0

        res["wacc"]            = round(wacc_val * 100, 2)
        res["capex"]           = capex
        res["working_capital"] = working_cap
    except:
        pass
    return res


# ─────────────────────────────────────────
# Stock Analysis
# ─────────────────────────────────────────
@app.get("/analyze")
def get_stock_analysis(ticker: str = Query(..., description="رمز الشركة")):
    try:
        stock = yf.Ticker(ticker)
        info  = stock.info

        if not info or 'longName' not in info:
            return {"error": "Ticker not found"}

        next_earn, ex_div = "N/A", "N/A"
        try:
            cal = stock.calendar
            if isinstance(cal, dict):
                if cal.get('Earnings Date'):
                    next_earn = cal['Earnings Date'][0].strftime('%Y-%m-%d')
                if cal.get('Ex-Dividend Date'):
                    ex_div = cal['Ex-Dividend Date'].strftime('%Y-%m-%d')
        except:
            pass

        a_inc = stock.financials
        chart_data = []
        cagr_rev = 0
        if not a_inc.empty:
            for col in a_inc.columns[:min(4, len(a_inc.columns))]:
                chart_data.append({
                    "year": col.year,
                    "revenue":    safe_float(a_inc.loc['Total Revenue', col]) if 'Total Revenue' in a_inc.index else 0,
                    "net_income": safe_float(a_inc.loc['Net Income', col])    if 'Net Income'    in a_inc.index else 0
                })
            if len(a_inc.columns) >= 4:
                r_now  = safe_float(a_inc.iloc[0, 0])
                r_past = safe_float(a_inc.iloc[0, 3])
                if r_past > 0:
                    cagr_rev = round(((r_now / r_past) ** (1/3) - 1) * 100, 2)

        q_inc = stock.quarterly_financials
        q_bal = stock.quarterly_balance_sheet
        q_cf  = stock.quarterly_cashflow

        metrics = {}
        if not q_inc.empty:
            ttm_inc = q_inc.iloc[:, :min(4, len(q_inc.columns))].sum(axis=1)
            metrics = calculate_metrics(
                ttm_inc,
                q_bal.iloc[:, 0] if not q_bal.empty else {},
                q_cf.iloc[:, 0]  if not q_cf.empty  else {},
                info
            )

        return {
            "ticker":       ticker,
            "name":         info.get('longName'),
            "sector":       info.get('sector'),
            "currentPrice": safe_float(info.get('currentPrice', info.get('regularMarketPrice'))),
            "week_52_high": safe_float(info.get('fiftyTwoWeekHigh')),
            "week_52_low":  safe_float(info.get('fiftyTwoWeekLow')),
            "fair_value":   calculate_fair_value(info),
            "pe_ratio":     safe_float(info.get('trailingPE')),
            "peg_ratio":    safe_float(info.get('pegRatio')),
            "ev_ebitda":    safe_float(info.get('enterpriseToEbitda')),
            "beta":         safe_float(info.get('beta')),
            "insider_percentage": safe_float(info.get('heldPercentInsiders', 0)) * 100,
            "next_earnings_date": next_earn,
            "ex_dividend_date":   ex_div,
            "cagr_revenue_3y":    cagr_rev,
            **metrics,
            "history_charts": chart_data[::-1]
        }

    except Exception as e:
        return {"error": str(e)}
