from __future__ import annotations

from io import BytesIO
from typing import Optional
from decimal import Decimal

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

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

    # build PDF
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    y = h - 48
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, "FloorIQ Nepal — Broker Report (Analytics only)")
    y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(40, y, "SEBON Disclaimer: Analytics only, no trading advice. Descriptive insights based on public floorsheet data.")
    y -= 22

    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, y, f"Broker {broker_id}  |  Business date: {summary.get('business_date')}")
    y -= 18

    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Summary")
    y -= 14
    c.setFont("Helvetica", 10)

    def line(k, v):
        nonlocal y
        c.drawString(50, y, f"{k}: {v}")
        y -= 12

    line("Net amount", _fnum(summary.get("net_amount")))
    line("Total buy amount", _fnum(summary.get("total_buy_amount")))
    line("Total sell amount", _fnum(summary.get("total_sell_amount")))
    line("Trades (buy)", _fnum(summary.get("trades_buy_count")))
    line("Trades (sell)", _fnum(summary.get("trades_sell_count")))
    line("Unique symbols (buy)", _fnum(summary.get("unique_symbols_buy")))
    line("Unique symbols (sell)", _fnum(summary.get("unique_symbols_sell")))

    y -= 6
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Top symbols (buy)")
    y -= 14
    c.setFont("Helvetica", 10)
    if not top_symbols:
        c.drawString(50, y, "No data")
        y -= 12
    else:
        c.drawString(50, y, "Symbol")
        c.drawString(160, y, "Amount")
        c.drawString(280, y, "Share %")
        y -= 12
        for sym, amt, pct in top_symbols[:10]:
            if y < 80:
                c.showPage()
                y = h - 48
                c.setFont("Helvetica", 10)
            c.drawString(50, y, str(sym))
            c.drawString(160, y, f"{amt:,.2f}")
            c.drawString(280, y, f"{pct:.2f}%")
            y -= 12

    y -= 6
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Anomalies (broker)")
    y -= 14
    c.setFont("Helvetica", 10)
    if not arows:
        c.drawString(50, y, "No anomalies")
        y -= 12
    else:
        for atype, score, details in arows:
            if y < 80:
                c.showPage()
                y = h - 48
                c.setFont("Helvetica", 10)
            d = (details or "")
            d = d.replace("\n", " ")[:160]
            c.drawString(50, y, f"{atype} | score={float(score):.2f}")
            y -= 12
            c.drawString(60, y, d)
            y -= 14

    c.showPage()
    c.save()
    buf.seek(0)

    await audit_event(redis, int(broker_id), "download_pdf", request, {"business_date": str(summary.get("business_date"))})

    filename = f"flooriq_broker_{broker_id}_{summary.get('business_date')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
