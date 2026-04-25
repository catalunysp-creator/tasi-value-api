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
        # البيانات الأساسية
        rev = income.get('Total Revenue', 0)
        ni = income.get('Net Income', 0)
        ebit = income.get('Operating Income', income.get('EBIT', 0))
        ebitda = income.get('EBITDA', info.get('ebitda', ebit))
        
        equity = balance.get('Stockholders Equity', balance.get('Total Equity Gross Minority Interest', 0))
        total_debt = balance.get('Total Debt', 0)
        net_debt = info.get('netDebt', total_debt - balance.get('Cash And Cash Equivalents', 0))
        
        op_cash = cashflow.get('Operating Cash Flow', 0)
        capex = abs(cashflow.get('Capital Expenditure', 0))
        fcf = op_cash - capex

        # مؤشرات جديدة: تغطية الفوائد ورأس المال العامل
        interest_expense = abs(income.get('Interest Expense', 0))
        working_capital = balance.get('Current Assets', 0) - balance.get('Current Liabilities', 0)

        # 1. مؤشرات الجودة والكفاءة
        roic = (ebit / (equity + total_debt)) * 100 if (equity + total_debt) > 0 else 0
        roe = (ni / equity) * 100 if equity > 0 else 0
        roe_roic_ratio = roe / roic if roic > 0 else 0
        ebit_margin = (ebit / rev) * 100 if rev > 0 else 0

        # 2. مؤشرات الملاءة والمخاطر
        net_debt_ebitda = net_debt / ebitda if ebitda > 0 else 0
        interest_coverage = ebit / interest_expense if interest_expense > 0 else 0

        # 3. تقدير WACC (بناءً على معايير السوق السعودي التقريبية)
        rf = 0.045  # معدل خالي من المخاطر 4.5%
        erp = 0.055 # علاوة مخاطر السوق 5.5%
        beta = info.get('beta', 1.0) or 1.0
        tax = 0.20  # نسبة الزكاة/الضريبة التقديرية
        
        cost_of_equity = rf + (beta * erp)
        cost_of_debt = (interest_expense / total_debt) if total_debt > 0 else rf
        
        m_cap = info.get('marketCap', 0)
        total_cap = m_cap + total_debt
        wacc = 0
        if total_cap > 0:
            w_eq = m_cap / total_cap
            w_d = total_debt / total_cap
            wacc = (w_eq * cost_of_equity) + (w_d * cost_of_debt * (1 - tax))

        return {
            "ROIC": round(float(roic), 2),
            "ROE": round(float(roe), 2),
            "ROE_ROIC_Ratio": round(float(roe_roic_ratio), 2),
            "FCF_Yield": round(float(fcf_yield_calc(fcf, info)), 2),
            "D_E": round(float(total_debt / equity), 2) if equity > 0 else 0,
            "EBIT_Margin": round(float(ebit_margin), 2),
            "Net_Debt_EBITDA": round(float(net_debt_ebitda), 2),
            "Interest_Coverage": round(float(interest_coverage), 2),
            "CapEx": float(capex),
            "Working_Capital": float(working_capital),
            "WACC": round(float(wacc * 100), 2)
        }
    except: return {}

def fcf_yield_calc(fcf, info):
    mcap = info.get('marketCap')
    return (fcf / mcap) * 100 if mcap else 0

@app.get("/analyze")
def get_stock_analysis(ticker: str = Query(..., description="رمز الشركة")):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        calendar = stock.calendar
        
        # التقويم المالي
        next_earn = "N/A"
        ex_div = "N/A"
        if isinstance(calendar, dict):
            earn = calendar.get('Earnings Date')
            if earn: next_earn = earn[0].strftime('%Y-%m-%d')
            div = calendar.get('Ex-Dividend Date')
            if div: ex_div = div.strftime('%Y-%m-%d')

        # البيانات المالية والنمو
        a_inc = stock.financials
        cagr_rev = 0
        chart_data = []
        if not a_inc.empty:
            for col in a_inc.columns[:4]:
                chart_data.append({"year": col.year, "revenue": float(a_inc.loc['Total Revenue', col]), "net_income": float(a_inc.loc['Net Income', col])})
            if len(a_inc.columns) >= 4:
                cagr_rev = ((a_inc.iloc[0,0] / a_inc.iloc[0,3])**(1/3)-1)*100

        q_inc, q_bal, q_cf = stock.quarterly_financials, stock.quarterly_balance_sheet, stock.quarterly_cashflow
        ttm_inc = q_inc.iloc[:, :min(4, len(q_inc.columns))].sum(axis=1)
        metrics = calculate_metrics(ttm_inc, q_bal.iloc[:, 0], q_cf.iloc[:, :min(4, len(q_cf.columns))].sum(axis=1), info)

        return {
            "ticker": ticker,
            "name": info.get('longName'),
            "sector": info.get('sector'),
            "currentPrice": info.get('currentPrice'),
            "pe_ratio": info.get('trailingPE'),
            "peg_ratio": info.get('pegRatio'),
            "ev_ebitda": info.get('enterpriseToEbitda'),
            "beta": info.get('beta'),
            "insider_percentage": info.get('insidersPercentHeld'),
            "next_earnings_date": next_earn,
            "ex_dividend_date": ex_div,
            "cagr_revenue_3y": round(float(cagr_rev), 2),
            "analysis": metrics,
            "history_charts": chart_data[::-1]
        }
    except Exception as e: return {"error": str(e)}
