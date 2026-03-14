from __future__ import annotations

from io import BytesIO
from typing import Optional
from decimal import Decimal
import json

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


def _fmt(v, decimals=2):
    try:
        f = float(v)
        if abs(f) >= 1e7:
            return f"{f / 1e7:.2f} Cr"
        if abs(f) >= 1e5:
            return f"{f / 1e5:.2f} L"
        return f"{f:,.{decimals}f}"
    except Exception:
        return str(v) if v is not None else "-"


def _pdf_safe(text):
    if text is None:
        return "-"
    return str(text).replace("—", "-").replace("₨", "Rs.").replace("✓", "OK")

def _friendly_anomaly_label(anomaly_type: str) -> str:
    mapping = {
        "wash_candidate_roundtrip": "Candidate round-trip pattern",
        "wash_candidate_self_trade": "Candidate self-trade pattern",
        "wash_candidate_circular_flow": "Candidate circular flow pattern",
        "wash_trading_v2.1_circular": "Legacy circular-flow heuristic flag",
        "wash_counterparty_dominance": "Counterparty concentration flag",
        "broker_net_amount_spike": "Net amount spike flag",
        "symbol_turnover_spike": "Turnover spike flag",
        "isolation_forest_anomaly": "ML anomaly flag",
    }
    return mapping.get(anomaly_type, anomaly_type.replace("_", " ").strip())


def _anomaly_details_summary(label, details) -> str:
    try:
        if isinstance(details, dict):
            d = details
        elif isinstance(details, str) and details.strip():
            d = json.loads(details)
        else:
            d = {}
    except Exception:
        d = {}

    if not isinstance(d, dict) or not d:
        return ""

    if label == "Candidate round-trip pattern":
        sym = d.get("symbol") or "-"
        cp = d.get("counterparty") or "-"
        risk = d.get("risk_band") or "-"
        hp = d.get("historical_pair_days", 0)
        hc = d.get("heuristic_components") or {}
        lag = hc.get("time_delta_minutes", "-")
        px = hc.get("price_deviation_pct", "-")
        return f"Symbol: {sym} | Counterparty: Broker {cp} | Risk: {risk} | Lag(min): {lag} | Px dev%: {px} | Historical pair days: {hp}"

    if label == "Candidate self-trade pattern":
        sym = d.get("symbol") or "-"
        risk = d.get("risk_band") or "-"
        hc = d.get("heuristic_components") or {}
        cnt = hc.get("self_trade_count", "-")
        amt = hc.get("total_self_amount")
        amt_txt = _fmt(amt) if amt is not None else "-"
        return f"Symbol: {sym} | Risk: {risk} | Self trades: {cnt} | Total amount: {amt_txt}"

    if label == "Legacy circular-flow heuristic flag":
        pattern = d.get("pattern") or "-"
        flow = d.get("total_circular_flow")
        flow_txt = _fmt(flow) if flow is not None else "-"
        return f"Legacy pattern: {pattern} | Circular flow: {flow_txt}"

    if label == "Counterparty concentration flag":
        cp = d.get("counterparty") or "-"
        share = d.get("share")
        turnover = d.get("broker_turnover")
        linked = d.get("with_counterparty_amount")
        share_txt = f"{float(share) * 100:.2f}%" if share is not None else "-"
        turnover_txt = _fmt(turnover) if turnover is not None else "-"
        linked_txt = _fmt(linked) if linked is not None else "-"
        return f"Counterparty: Broker {cp} | Share: {share_txt} | Broker turnover: {turnover_txt} | Linked amount: {linked_txt}"

    if label == "Net amount spike flag":
        z = d.get("z")
        net_amount = d.get("net_amount")
        z_txt = f"{float(z):.2f}" if z is not None else "-"
        net_txt = _fmt(net_amount) if net_amount is not None else "-"
        return f"Z-score: {z_txt} | Net amount: {net_txt}"

    if label == "Turnover spike flag":
        z = d.get("z")
        total_amount = d.get("total_amount")
        z_txt = f"{float(z):.2f}" if z is not None else "-"
        amt_txt = _fmt(total_amount) if total_amount is not None else "-"
        return f"Z-score: {z_txt} | Total amount: {amt_txt}"

    if label == "ML anomaly flag":
        if_score = d.get("if_score")
        threshold = d.get("threshold")
        if_txt = f"{float(if_score):.3f}" if if_score is not None else "-"
        th_txt = f"{float(threshold):.3f}" if threshold is not None else "-"
        return f"IF score: {if_txt} | Threshold: {th_txt} | Model: IsolationForest"

    parts = []
    if d.get("counterparty") is not None:
        parts.append(f"Counterparty: Broker {d['counterparty']}")
    if d.get("symbol"):
        parts.append(f"Symbol: {d['symbol']}")
    if d.get("z") is not None:
        parts.append(f"Z: {float(d['z']):.2f}")
    return " | ".join(parts[:6])


@router.get("/reports/broker/{broker_id}.pdf")
async def broker_report_pdf(
    request: Request,
    broker_id: int,
    business_date: Optional[str] = None,
    conn: psycopg.Connection = Depends(db_conn),
    redis=Depends(redis_client),
    _=Depends(enforce_broker_access),
):
    with conn.cursor() as cur:
        # Latest date
        if business_date is None:
            cur.execute("SELECT MAX(business_date) FROM public.broker_daily_features")
            row0 = cur.fetchone()
            business_date = row0[0] if row0 else None

        # Summary row
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
            WHERE business_date = %s AND broker_id = %s
            """,
            (business_date, str(broker_id)),
        )
        row = cur.fetchone()
        if not row:
            row = (business_date, str(broker_id), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        keys = [
            "business_date", "broker_id",
            "total_buy_qty", "total_sell_qty", "total_buy_amount", "total_sell_amount",
            "net_qty", "net_amount",
            "unique_symbols_buy", "unique_symbols_sell",
            "trades_buy_count", "trades_sell_count",
            "avg_buy_price", "avg_sell_price",
        ]
        summary = dict(zip(keys, row))

        # Total buy amount
        cur.execute(
            "SELECT COALESCE(SUM(amount), 0)::numeric FROM public.trades "
            "WHERE business_date = %s AND buyer_member_id = %s",
            (business_date, str(broker_id)),
        )
        _row = cur.fetchone()
        total_buy_amt = (_row[0] if _row is not None else None) or Decimal("0")

        # Top symbols
        cur.execute(
            """
            SELECT stock_symbol, SUM(amount)::numeric AS amt
            FROM public.trades
            WHERE business_date = %s AND buyer_member_id = %s
            GROUP BY stock_symbol ORDER BY amt DESC LIMIT 10
            """,
            (business_date, str(broker_id)),
        )
        top_symbols = []
        for sym, amt in cur.fetchall():
            pct = float(amt / total_buy_amt * 100) if total_buy_amt else 0.0
            top_symbols.append((sym, float(amt or 0), round(pct, 2)))

        # HHI
        cur.execute(
            "SELECT SUM(amount)::numeric FROM public.trades "
            "WHERE business_date = %s AND buyer_member_id = %s "
            "GROUP BY stock_symbol",
            (business_date, str(broker_id)),
        )
        hhi = 0.0
        if total_buy_amt:
            for (a,) in cur.fetchall():
                share = float(a / total_buy_amt)
                hhi += share * share
            hhi *= 10000.0

        top1_pct = top_symbols[0][2] if top_symbols else 0.0
        if hhi >= 2500 or top1_pct >= 40:
            risk = "HIGH"
        elif hhi >= 1500 or top1_pct >= 25:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        # Anomalies
        cur.execute(
            """
            SELECT anomaly_type, score, details
            FROM public.anomalies
            WHERE business_date = %s AND entity_type = 'broker' AND entity_id = %s
            ORDER BY score DESC LIMIT 20
            """,
            (business_date, str(broker_id)),
        )
        arows = [
            (_friendly_anomaly_label(anomaly_type), score, details)
            for anomaly_type, score, details in cur.fetchall()
        ]

        # Sector flows (today vs previous trading day)
        cur.execute("SELECT MAX(business_date) FROM public.trades WHERE business_date < %s", (business_date,))
        prev_date = cur.fetchone()[0]

        cur.execute(
            """
            SELECT stock_symbol, COALESCE(SUM(amount),0)::numeric AS amt
            FROM public.trades
            WHERE business_date=%s AND buyer_member_id=%s
            GROUP BY stock_symbol
            """,
            (business_date, str(broker_id)),
        )
        buys_today = {(sym or "").strip().upper(): float(amt or 0) for sym, amt in cur.fetchall()}

        cur.execute(
            """
            SELECT stock_symbol, COALESCE(SUM(amount),0)::numeric AS amt
            FROM public.trades
            WHERE business_date=%s AND seller_member_id=%s
            GROUP BY stock_symbol
            """,
            (business_date, str(broker_id)),
        )
        sells_today = {(sym or "").strip().upper(): float(amt or 0) for sym, amt in cur.fetchall()}

        buys_prev, sells_prev = {}, {}
        if prev_date is not None:
            cur.execute(
                """
                SELECT stock_symbol, COALESCE(SUM(amount),0)::numeric AS amt
                FROM public.trades
                WHERE business_date=%s AND buyer_member_id=%s
                GROUP BY stock_symbol
                """,
                (prev_date, str(broker_id)),
            )
            buys_prev = {(sym or "").strip().upper(): float(amt or 0) for sym, amt in cur.fetchall()}

            cur.execute(
                """
                SELECT stock_symbol, COALESCE(SUM(amount),0)::numeric AS amt
                FROM public.trades
                WHERE business_date=%s AND seller_member_id=%s
                GROUP BY stock_symbol
                """,
                (prev_date, str(broker_id)),
            )
            sells_prev = {(sym or "").strip().upper(): float(amt or 0) for sym, amt in cur.fetchall()}

        sector_map = {
            "AKJCL": "Hydro Power", "API": "Hydro Power", "SYPNL": "Hydro Power",
            "SSHL": "Hydro Power", "SAIL": "Hydro Power", "RIDI": "Hydro Power",
            "NGPL": "Hydro Power", "SAHAS": "Hydro Power", "SMHL": "Hydro Power",
            "NMB": "Commercial Banks"
        }

        sector_flows = {}
        for sym in set(buys_today) | set(sells_today):
            sec = sector_map.get(sym, "Unknown")
            net = buys_today.get(sym, 0.0) - sells_today.get(sym, 0.0)
            prev_net = buys_prev.get(sym, 0.0) - sells_prev.get(sym, 0.0)
            rec = sector_flows.get(sec, {"net_amount": 0.0, "prev_net_amount": 0.0})
            rec["net_amount"] += net
            rec["prev_net_amount"] += prev_net
            sector_flows[sec] = rec

        sector_rows = []
        for sec, rec in sector_flows.items():
            sector_rows.append((
                sec,
                round(rec["net_amount"], 2),
                round(rec["prev_net_amount"], 2),
                round(rec["net_amount"] - rec["prev_net_amount"], 2),
            ))
        sector_rows.sort(key=lambda x: abs(x[1]), reverse=True)
        sector_rows = sector_rows[:6]

    # ── PDF ───────────────────────────────────────────────────────────────────
    try:
        from fpdf import FPDF
    except Exception as exc:
        raise RuntimeError(f"PDF dependency missing: {exc}") from exc

    # Colors
    C_BG      = (8,   12,  16)
    C_SURFACE = (15,  25,  35)
    C_GREEN   = (0,   208, 132)
    C_AMBER   = (245, 166, 35)
    C_RED     = (255, 77,  77)
    C_TEXT    = (226, 232, 240)
    C_MUTED   = (100, 116, 139)

    risk_color = C_RED if risk == "HIGH" else (C_AMBER if risk == "MEDIUM" else C_GREEN)

    # ── Custom PDF class ──────────────────────────────────────────────────────
    class FloorIQPDF(FPDF):
        def header(self):
            # Dark header bg
            self.set_fill_color(*C_BG)
            self.rect(0, 0, 210, 22, "F")
            # Green left stripe
            self.set_fill_color(*C_GREEN)
            self.rect(0, 0, 4, 22, "F")
            # Text
            self.set_xy(10, 7)
            self.set_text_color(*C_GREEN)
            self.set_font("Helvetica", "B", 13)
            self.cell(32, 8, _pdf_safe("FloorIQ"))
            self.set_text_color(*C_MUTED)
            self.set_font("Helvetica", "", 10)
            self.cell(0, 8, _pdf_safe(" Nepal  \u2014  Broker Intelligence Report"))
            self.set_y(24)

        def footer(self):
            self.set_y(-12)
            self.set_fill_color(*C_BG)
            self.rect(0, self.get_y(), 210, 12, "F")
            self.set_text_color(*C_MUTED)
            self.set_font("Helvetica", "", 8)
            self.cell(
                0, 8,
                f"Analytics only. No trading advice. NEPSE public floorsheet data.   "
                f"Page {self.page_no()}",
                align="C",
            )

        def section_title(self, title: str):
            self.ln(5)
            y = self.get_y()
            self.set_fill_color(*C_SURFACE)
            self.rect(10, y, 190, 8, "F")
            self.set_fill_color(*C_GREEN)
            self.rect(10, y, 3, 8, "F")
            self.set_xy(15, y)
            self.set_text_color(*C_GREEN)
            self.set_font("Helvetica", "B", 9)
            self.cell(0, 8, _pdf_safe(title.upper()))
            self.ln(10)

        def kv_row(self, key: str, value: str, shade: bool = False):
            y = self.get_y()
            self.set_fill_color(*C_SURFACE)
            self.rect(10, y, 190, 7, "F")
            self.set_xy(13, y)
            self.set_text_color(*C_MUTED)
            self.set_font("Helvetica", "", 9)
            self.cell(85, 7, _pdf_safe(key))
            self.set_text_color(*C_TEXT)
            self.set_font("Helvetica", "B", 9)
            self.cell(0, 7, _pdf_safe(value))
            self.ln()

    pdf = FloorIQPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(10, 26, 10)
    pdf.add_page()

    # ── TITLE BLOCK ───────────────────────────────────────────────────────────
    y_t = 26
    pdf.set_fill_color(*C_SURFACE)
    pdf.rect(10, y_t, 190, 22, "F")
    pdf.set_fill_color(*C_GREEN)
    pdf.rect(10, y_t, 3, 22, "F")

    # Sub label
    pdf.set_xy(16, y_t + 2)
    pdf.set_text_color(*C_MUTED)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, "BROKER ANALYTICS REPORT")

    # Broker name
    pdf.set_xy(16, y_t + 8)
    pdf.set_text_color(*C_TEXT)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(120, 8, _pdf_safe(f"Broker #{broker_id}"))

    # Risk badge
    pdf.set_xy(152, y_t + 6)
    pdf.set_fill_color(*risk_color)
    pdf.set_text_color(*C_BG)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(46, 10, _pdf_safe(f"RISK: {risk}"), align="C", fill=True)

    # Date / HHI
    pdf.set_xy(16, y_t + 17)
    pdf.set_text_color(*C_MUTED)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(
        0, 5,
         _pdf_safe(f"Date: {summary.get('business_date')}   |   HHI: {hhi:.0f}   |   Top1: {top1_pct:.1f}%"),
    )

    # Disclaimer
    pdf.set_xy(10, y_t + 26)
    pdf.set_text_color(*C_MUTED)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        190, 5,
        _pdf_safe("SEBON Safe: Descriptive market analytics only. Does not constitute investment advice or trading recommendations."),
    )

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    pdf.section_title("Daily Summary")

    rows_kv = [
        ("Net Amount",            _fmt(summary.get("net_amount"))),
        ("Total Buy Amount",      _fmt(summary.get("total_buy_amount"))),
        ("Total Sell Amount",     _fmt(summary.get("total_sell_amount"))),
        ("Total Buy Qty",         _fmt(summary.get("total_buy_qty"), 0)),
        ("Total Sell Qty",        _fmt(summary.get("total_sell_qty"), 0)),
        ("Trades (Buy)",          str(int(_fnum(summary.get("trades_buy_count")) or 0))),
        ("Trades (Sell)",         str(int(_fnum(summary.get("trades_sell_count")) or 0))),
        ("Unique Symbols (Buy)",  str(int(_fnum(summary.get("unique_symbols_buy")) or 0))),
        ("Unique Symbols (Sell)", str(int(_fnum(summary.get("unique_symbols_sell")) or 0))),
        ("Avg Buy Price",         _fmt(summary.get("avg_buy_price"))),
        ("Avg Sell Price",        _fmt(summary.get("avg_sell_price"))),
    ]
    for i, (k, v) in enumerate(rows_kv):
        pdf.kv_row(k, v, shade=(i % 2 == 0))

    # ── TOP SYMBOLS ───────────────────────────────────────────────────────────
    pdf.section_title("Top Symbols (Buy Side)")

    if not top_symbols:
        pdf.set_text_color(*C_MUTED)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, _pdf_safe("No data available"))
        pdf.ln()
    else:
        # Header row
        y = pdf.get_y()
        pdf.set_fill_color(*C_BG)
        pdf.rect(10, y, 190, 7, "F")
        pdf.set_xy(13, y)
        pdf.set_text_color(*C_MUTED)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(28, 7, _pdf_safe("SYMBOL"))
        pdf.cell(65, 7, _pdf_safe("AMOUNT"))
        pdf.cell(32, 7, _pdf_safe("SHARE %"))
        pdf.cell(0,  7, _pdf_safe("BAR"))
        pdf.ln()

        for i, (sym, amt, pct) in enumerate(top_symbols[:6]):
            y = pdf.get_y()
            pdf.set_fill_color(*C_SURFACE)
            pdf.rect(10, y, 190, 7, "F")
            pdf.set_xy(13, y)
            pdf.set_text_color(*C_GREEN)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(28, 7, _pdf_safe(sym))
            pdf.set_text_color(*C_TEXT)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(65, 7, _pdf_safe(f"Rs. {amt:,.2f}"))
            pdf.cell(32, 7, _pdf_safe(f"{pct:.2f}%"))
            # Mini bar
            bx = pdf.get_x()
            bw = min(pct / 100 * 50, 50)
            if bw > 0.5:
                pdf.set_fill_color(*C_GREEN)
                pdf.rect(bx, y + 2, bw, 3, "F")
            pdf.ln()

    # ── SECTOR FLOWS ──────────────────────────────────────────────────────────
    if sector_rows:
        pdf.section_title("Sector Flows")
        y = pdf.get_y()
        pdf.set_fill_color(*C_BG)
        pdf.rect(10, y, 190, 7, "F")
        pdf.set_xy(13, y)
        pdf.set_text_color(*C_MUTED)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(55, 7, _pdf_safe("SECTOR"))
        pdf.cell(40, 7, _pdf_safe("NET"))
        pdf.cell(40, 7, _pdf_safe("PREV NET"))
        pdf.cell(0, 7, _pdf_safe("DELTA"))
        pdf.ln()

        for sec, net_amt, prev_amt, delta_amt in sector_rows:
            y = pdf.get_y()
            pdf.set_fill_color(*C_SURFACE)
            pdf.rect(10, y, 190, 7, "F")
            pdf.set_xy(13, y)
            pdf.set_text_color(*C_TEXT)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(55, 7, _pdf_safe(sec))
            pdf.cell(40, 7, _pdf_safe(_fmt(net_amt)))
            pdf.cell(40, 7, _pdf_safe(_fmt(prev_amt)))
            pdf.set_text_color(*(C_GREEN if delta_amt >= 0 else C_RED))
            pdf.cell(0, 7, _pdf_safe(f"{'+' if delta_amt >= 0 else ''}{_fmt(delta_amt)}"))
            pdf.ln()

    # ── ANOMALIES ─────────────────────────────────────────────────────────────
    pdf.section_title("Anomaly Detection Flags")

    if not arows:
        y = pdf.get_y()
        pdf.set_fill_color(10, 28, 18)
        pdf.rect(10, y, 190, 8, "F")
        pdf.set_xy(13, y)
        pdf.set_text_color(*C_GREEN)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(0, 8, _pdf_safe("No anomalies detected for this date."))
        pdf.ln()
    else:
        for label, score, details in arows:
            acolor = C_MUTED
            score_f = float(score)

            y = pdf.get_y()
            pdf.set_fill_color(*C_SURFACE)
            pdf.rect(10, y, 190, 11, "F")
            pdf.set_fill_color(*acolor)
            pdf.rect(10, y, 3, 11, "F")

            pdf.set_xy(15, y + 1.5)
            pdf.set_text_color(*acolor)
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(120, 4, _pdf_safe(label))

            pdf.set_xy(158, y + 1.5)
            pdf.set_fill_color(*acolor)
            pdf.set_text_color(*C_BG)
            pdf.set_font("Helvetica", "B", 7)
            pdf.cell(40, 4, _pdf_safe(f"Score: {score_f:.3f}"), align="C", fill=True)

            # Details
            pdf.set_xy(15, y + 6.5)
            pdf.set_text_color(*C_MUTED)
            pdf.set_font("Helvetica", "", 7)
            det_str = _anomaly_details_summary(label, details)
            pdf.cell(0, 3, _pdf_safe(det_str[:110]))
            pdf.ln(4)

    # ── CLOSING NOTE ──────────────────────────────────────────────────────────
    pdf.ln(4)
    pdf.set_xy(10, pdf.get_y())
    pdf.set_text_color(*C_MUTED)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        190, 5,
        _pdf_safe("Generated by FloorIQ Nepal. For informational purposes only. Risk scores are heuristic-based and not a definitive assessment of broker conduct."),
    )

    # ── Stream response ────────────────────────────────────────────────────────
    # fpdf2 v2: output() returns bytearray directly — no dest= parameter
    raw = pdf.output()
    buf = BytesIO(bytes(raw))
    buf.seek(0)

    await audit_event(
        redis, int(broker_id), "download_pdf", request,
        {"business_date": str(summary.get("business_date"))},
    )

    filename = f"flooriq_broker_{broker_id}_{summary.get('business_date')}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )