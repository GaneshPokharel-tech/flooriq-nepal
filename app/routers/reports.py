from __future__ import annotations

from io import BytesIO
from typing import Optional
from decimal import Decimal

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.dependencies import db_conn, redis_client
from app.broker_auth import enforce_broker_access, audit_event

router = APIRouter(tags=["reports"])


def _fnum(v):
    try:
        return float(v)
    except Exception:
        return v


@router.get("/reports/broker/{broker_id}.pdf")
async def broker_report_pdf(
    request: Request,
    broker_id: int,
    business_date: Optional[str] = None,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(enforce_broker_access),
):
    # Minimal broker report PDF (Phase 1: no DB changes)
    with conn.cursor() as cur:
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
            business_date = cur.fetchone()[0]

        cur.execute(
            """
            SELECT
              business_date, broker_id,
              total_buy_qty, total_sell_qty, total_buy_amount, total_sell_amount,
              net_qty, net_amount,
              unique_symbols_buy, unique_symbols_sell,
              trades_buy_count, trades_sell_count,
              avg_buy_price, avg_sell_price
            FROM public.broker_daily_features
            WHERE business_date=%s AND broker_id=%s
            """,
            (business_date, str(broker_id)),
        )
        row = cur.fetchone()
        if not row:
            # keep response valid PDF-ish message
            row = (business_date, str(broker_id), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        keys = [
            "business_date","broker_id",
            "total_buy_qty","total_sell_qty","total_buy_amount","total_sell_amount",
            "net_qty","net_amount",
            "unique_symbols_buy","unique_symbols_sell",
            "trades_buy_count","trades_sell_count",
            "avg_buy_price","avg_sell_price",
        ]
        summary = dict(zip(keys, row))

        # top symbols by buy amount
        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0)::numeric
            FROM public.trades
            WHERE business_date=%s AND buyer_member_id=%s
            """,
            (business_date, str(broker_id)),
        )
        total_buy_amt = cur.fetchone()[0] or Decimal("0")

        cur.execute(
            """
            SELECT stock_symbol, SUM(amount)::numeric AS amt
            FROM public.trades
            WHERE business_date=%s AND buyer_member_id=%s
            GROUP BY stock_symbol
            ORDER BY amt DESC
            LIMIT 10
            """,
            (business_date, str(broker_id)),
        )
        top_rows = cur.fetchall()

        top_symbols = []
        for sym, amt in top_rows:
            pct = float((amt / total_buy_amt) * 100) if total_buy_amt and total_buy_amt != 0 else 0.0
            top_symbols.append((sym, float(amt or 0), round(pct, 2)))

        # anomalies (broker)
        cur.execute(
            """
            SELECT anomaly_type, score, details
            FROM public.anomalies
            WHERE business_date=%s AND entity_type='broker' AND entity_id=%s
            ORDER BY score DESC
            LIMIT 20
            """,
            (business_date, str(broker_id)),
        )
        arows = cur.fetchall()

    # build PDF using fpdf2 (lightweight; safe for Railway boot)
    try:
        from fpdf import FPDF
    except Exception as e:
        # keep API alive even if PDF deps missing
        raise RuntimeError(f"PDF dependency missing: {type(e).__name__}")

    class _PDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 12)
            self.cell(0, 8, "FloorIQ Nepal — Broker Report (Analytics only)", ln=1)
            self.set_font("Helvetica", "", 9)
            self.multi_cell(
                0,
                5,
                "SEBON Disclaimer: Analytics only, no trading advice. Descriptive insights based on public floorsheet data.",
            )
            self.ln(2)

    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Broker {broker_id}  |  Business date: {summary.get('business_date')}", ln=1)
    pdf.ln(1)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Summary", ln=1)
    pdf.set_font("Helvetica", "", 10)

    def line(k, v):
        pdf.cell(0, 5, f"{k}: {v}", ln=1)

    line("Net amount", _fnum(summary.get("net_amount")))
    line("Total buy amount", _fnum(summary.get("total_buy_amount")))
    line("Total sell amount", _fnum(summary.get("total_sell_amount")))
    line("Trades (buy)", _fnum(summary.get("trades_buy_count")))
    line("Trades (sell)", _fnum(summary.get("trades_sell_count")))
    line("Unique symbols (buy)", _fnum(summary.get("unique_symbols_buy")))
    line("Unique symbols (sell)", _fnum(summary.get("unique_symbols_sell")))

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Top symbols (buy)", ln=1)
    pdf.set_font("Helvetica", "", 10)

    if not top_symbols:
        pdf.cell(0, 5, "No data", ln=1)
    else:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(30, 6, "Symbol", border=0)
        pdf.cell(45, 6, "Amount", border=0)
        pdf.cell(25, 6, "Share %", border=0, ln=1)
        pdf.set_font("Helvetica", "", 10)
        for sym, amt, pct in top_symbols[:10]:
            pdf.cell(30, 5, str(sym), border=0)
            pdf.cell(45, 5, f"{amt:,.2f}", border=0)
            pdf.cell(25, 5, f"{pct:.2f}%", border=0, ln=1)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Anomalies (broker)", ln=1)
    pdf.set_font("Helvetica", "", 10)

    if not arows:
        pdf.cell(0, 5, "No anomalies", ln=1)
    else:
        for atype, score, details in arows:
            d = (details or "")
            d = d.replace("\n", " ")
            try:
                d = str(d)
            except Exception:
                d = ""
            pdf.set_font("Helvetica", "B", 9)
            pdf.multi_cell(0, 5, f"{atype} | score={float(score):.2f}")
            pdf.set_font("Helvetica", "", 9)
            pdf.multi_cell(0, 5, d[:240])
            pdf.ln(1)

    _out = pdf.output(dest="S")
    if isinstance(_out, (bytes, bytearray)):
        pdf_bytes = bytes(_out)
    else:
        pdf_bytes = str(_out).encode("latin-1")
    buf = BytesIO(pdf_bytes)
    buf.seek(0)

    await audit_event(redis, int(broker_id), "download_pdf", request, {"business_date": str(summary.get("business_date"))})

    filename = f"flooriq_broker_{broker_id}_{summary.get('business_date')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )

