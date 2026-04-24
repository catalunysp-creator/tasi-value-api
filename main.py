from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

        # الحسابات المالية الأساسية
        roic = (ebit / (equity + total_debt)) * 100 if (equity + total_debt) > 0 else 0
        roe = (ni / equity) * 100 if equity > 0 else 0
        fcf_yield = (fcf / info.get('marketCap', 1)) * 100 if info.get('marketCap') else 0
        d_e = total_debt / equity if equity > 0 else 0
        
        # نسبة ROE إلى ROIC (توضح أثر الرافعة المالية)
        roe_roic_ratio = roe / roic if roic > 0 else 0

        return {
            "Revenue": float(rev),
            "Net_Income": float(ni),
            "ROIC": round(float(roic), 2),
            "ROE": round(float(roe), 2),
            "ROE_ROIC_Ratio": round(float(roe_roic_ratio), 2),
            "FCF_Yield": round(float(fcf_yield), 2),
            "D_E": round(float(d_e), 2)
        }
    except:
        return {}

@app.get("/analyze")
def get_stock_analysis(ticker: str = Query(..., description="رمز الشركة مثل 2222.SR")):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # 1. البيانات السنوية للرسم البياني والـ CAGR
        a_inc = stock.financials
        chart_data = []
        cagr_rev = 0

        if not a_inc.empty:
            # تجهيز بيانات الرسم البياني لآخر 4 سنوات
            for col in a_inc.columns[:4]:
                year_label = col.year
                chart_data.append({
                    "year": year_label,
                    "revenue": float(a_inc.loc['Total Revenue', col]) if 'Total Revenue' in a_inc.index else 0,
                    "net_income": float(a_inc.loc['Net Income', col]) if 'Net Income' in a_inc.index else 0
                })
            
            # حساب CAGR الإيرادات لثلاث سنوات
            if len(a_inc.columns) >= 4:
                rev_now = a_inc.iloc[:, 0].get('Total Revenue', 0)
                rev_past = a_inc.iloc[:, 3].get('Total Revenue', 0)
                if rev_past > 0 and rev_now > 0:
                    cagr_rev = ((rev_now / rev_past) ** (1/3) - 1) * 100

        # 2. البيانات الربعية للـ TTM (الأداء الحالي)
        q_inc = stock.quarterly_financials
        q_bal = stock.quarterly_balance_sheet
        q_cf = stock.quarterly_cashflow
        
        if q_inc.empty:
            return {"error": "No quarterly data found"}

        ttm_count = min(4, len(q_inc.columns))
        ttm_inc = q_inc.iloc[:, :ttm_count].sum(axis=1)
        ttm_cf = q_cf.iloc[:, :ttm_count].sum(axis=1)
        
        metrics = calculate_metrics(ttm_inc, q_bal.iloc[:, 0], ttm_cf, info)
        
        return {
            "ticker": ticker,
            "name": info.get('longName'),
            "sector": info.get('sector'),
            "currentPrice": info.get('currentPrice'),
            "pe_ratio": info.get('trailingPE'),
            "peg_ratio": info.get('pegRatio'),
            "cagr_revenue_3y": round(float(cagr_rev), 2),
            "analysis": metrics,
            "history_charts": chart_data[::-1] # ترتيب تصاعدي للسنين
        }
    except Exception as e:
        return {"error": str(e)}
