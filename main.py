from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np

app = FastAPI()

# تفعيل CORS لكي يتمكن موقعك في Lovable/Bolt من الاتصال بالـ API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- منطق الحساب الخاص بك (نفس كودك مع تعديل بسيط) ---
def calculate_metrics(income, balance, cashflow, info):
    try:
        rev = income.get('Total Revenue', 0)
        ni = income.get('Net Income', 0)
        ebit = income.get('Operating Income', income.get('EBIT', 0))
        equity = balance.get('Stockholders Equity', balance.get('Total Equity Gross Minority Interest', 0))
        total_debt = balance.get('Total Debt', 0)
        op_cash = cashflow.get('Operating Cash Flow', 0)
        capex = abs(cashflow.get('Capital Expenditure', 0))
        fcf = op_cash - capex

        roic = (ebit / (equity + total_debt)) * 100 if (equity + total_debt) > 0 else 0
        roe = (ni / equity) * 100 if equity > 0 else 0
        fcf_yield = (fcf / info.get('marketCap', 1)) * 100 if info.get('marketCap') else 0
        d_e = total_debt / equity if equity > 0 else 0
        
        return {
            "Revenue": float(rev),
            "Net_Income": float(ni),
            "ROIC": round(float(roic), 2),
            "FCF_Yield": round(float(fcf_yield), 2),
            "D_E": round(float(d_e), 2)
        }
    except:
        return {}

# --- نقطة الاتصال (Endpoint) التي سيطلبها موقعك ---
@app.get("/analyze")
def get_stock_analysis(ticker: str = Query(..., description="رمز الشركة مثل 2222.SR")):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # جلب القوائم (نفس منطقك)
        q_inc = stock.quarterly_financials
        q_bal = stock.quarterly_balance_sheet
        q_cf = stock.quarterly_cashflow
        
        if q_inc.empty:
            return {"error": "No data found"}

        # حساب الـ TTM (آخر 4 أرباع)
        ttm_count = min(4, len(q_inc.columns))
        ttm_inc = q_inc.iloc[:, :ttm_count].sum(axis=1)
        ttm_cf = q_cf.iloc[:, :ttm_count].sum(axis=1)
        
        metrics = calculate_metrics(ttm_inc, q_bal.iloc[:, 0], ttm_cf, info)
        
        return {
            "ticker": ticker,
            "name": info.get('longName'),
            "sector": info.get('sector'),
            "currentPrice": info.get('currentPrice'),
            "analysis": metrics
        }
    except Exception as e:
        return {"error": str(e)}