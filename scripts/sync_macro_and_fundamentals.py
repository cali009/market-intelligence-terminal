"""
Sync Macro Observations & SEC EDGAR Fundamentals to Database
Persists official Bank of Canada Valet rates and SEC EDGAR XBRL facts into canonical tables.
"""

import sys
from pathlib import Path
from datetime import date

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import db
from src.data.boc_valet import BankOfCanadaValetAdapter
from src.data.sec_edgar import SecEdgarAdapter


def sync_macro():
    print("Syncing Bank of Canada Valet macro observations...")
    boc = BankOfCanadaValetAdapter()
    series_list = ["FXUSDCAD", "BD.CDN.10YR.DQ.YLD", "BD.CDN.2YR.DQ.YLD"]
    obs_list = boc.fetch_observations(series_list, recent=10)

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        query = """
            INSERT INTO macro_observation (series_id, observation_date, knowledge_at, value, source, provenance_metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id, observation_date) DO UPDATE SET
                value=excluded.value,
                knowledge_at=excluded.knowledge_at
        """
        data_tuples = [
            (
                o.series_id,
                o.observation_date.isoformat(),
                o.knowledge_at.isoformat(),
                o.value,
                o.source,
                str(o.provenance_metadata),
            )
            for o in obs_list
        ]
        cur.executemany(query, data_tuples)
        conn.commit()
        print(f"  Successfully synced {len(data_tuples)} Bank of Canada macro observations.")
    finally:
        conn.close()


def sync_fundamentals():
    print("Syncing SEC EDGAR XBRL fundamentals for US and Canadian corporate securities...")
    edgar = SecEdgarAdapter()
    securities = db.execute_query(
        "SELECT security_id, symbol, country, currency, cik_padded FROM security WHERE cik_padded IS NOT NULL AND is_active = 1;"
    )

    total_facts = 0
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        query = """
            INSERT INTO fundamental_fact (
                security_id, concept, period_end, filing_date, knowledge_at,
                fiscal_year, fiscal_period, form, value, currency, source, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(security_id, concept, period_end, filing_date, form) DO UPDATE SET
                value=excluded.value,
                knowledge_at=excluded.knowledge_at
        """

        for s in securities:
            sec_id = s["security_id"]
            sym = s["symbol"]
            cik = s["cik_padded"]
            curr = s.get("currency", "USD")

            try:
                print(f"  Extracting facts for {sym} (CIK {cik})...", end="", flush=True)
                facts = edgar.extract_canonical_fundamentals(security_id=sec_id, cik=cik, recent_years=3, default_currency=curr)
                data_tuples = [
                    (
                        f.security_id,
                        f.concept,
                        f.period_end.isoformat(),
                        f.filing_date.isoformat(),
                        f.knowledge_at.isoformat(),
                        f.fiscal_year,
                        f.fiscal_period,
                        f.form,
                        f.value,
                        f.currency,
                        f.source,
                        f.confidence,
                    )
                    for f in facts
                ]
                cur.executemany(query, data_tuples)
                conn.commit()
                print(f" OK ({len(data_tuples)} facts)")
                total_facts += len(data_tuples)
            except Exception as e:
                print(f" FAILED ({e})")

        print(f"  Successfully synced {total_facts} canonical fundamental facts from SEC EDGAR (Dual-Market US + Canada).")
    finally:
        conn.close()


if __name__ == "__main__":
    sync_macro()
    sync_fundamentals()
