#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Calcolatore accise UE da HTML TEDB - versione Streamlit

Uso locale:
    pip install streamlit beautifulsoup4 pandas
    streamlit run app.py

Uso su Streamlit Cloud:
    caricare in GitHub:
        - app.py
        - requirements.txt
        - i file .html/.htm TEDB nella stessa cartella
"""

from __future__ import annotations

import re
import csv
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup


@dataclass
class RateRule:
    country: str
    product: str
    subtype: str
    rate_eur: float
    unit: str
    abv_from: Optional[float] = None
    abv_to: Optional[float] = None
    vat: Optional[float] = None
    source_file: str = ""
    note: str = ""

    def label(self) -> str:
        rng = ""
        if self.abv_from is not None or self.abv_to is not None:
            lo = "-∞" if self.abv_from is None else f"{self.abv_from:g}%"
            hi = "+∞" if self.abv_to is None else f"{self.abv_to:g}%"
            rng = f" | ABV > {lo} ≤ {hi}"
        return f"{self.country} | {self.product} | {self.subtype} | {self.rate_eur:.4f} {self.unit}{rng}"


def clean_number(s: str) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip().replace("\xa0", " ").replace(" ", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def html_to_lines_from_text(raw: str) -> List[str]:
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text("\n")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def country_from_file(filename: str) -> str:
    return Path(filename).stem.replace("_", " ").replace("-", " ").strip().title()


def find_section(lines: List[str], start: str, end_candidates: List[str]) -> List[str]:
    start_i = None
    for i, line in enumerate(lines):
        if line.strip().lower() == start.lower():
            start_i = i
            break
    if start_i is None:
        return []
    end_i = len(lines)
    for j in range(start_i + 1, len(lines)):
        if any(lines[j].strip().lower() == e.lower() for e in end_candidates):
            end_i = j
            break
    return lines[start_i:end_i]


def parse_rate_line(line: str) -> Optional[float]:
    m = re.search(r"([0-9][0-9 .,'\u00a0]*)\s*EUR\b", line, re.I)
    return clean_number(m.group(1)) if m else None


def parse_percent_line(line: str) -> Optional[float]:
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*%", line)
    return clean_number(m.group(1)) if m else None


def parse_wine_like(country: str, source_file: str, lines: List[str], product: str) -> List[RateRule]:
    rules: List[RateRule] = []
    current_subtype = None
    i = 0

    while i < len(lines):
        line = lines[i]

        if line in {"Still", "Sparkling"}:
            current_subtype = line
            i += 1
            continue

        rate = parse_rate_line(line)

        if rate is not None and current_subtype:
            vat = parse_percent_line(lines[i + 1]) if i + 1 < len(lines) else None
            abv_from = None
            abv_to = None

            # Pattern frequente TEDB: aliquota / VAT / From % / To %
            if i + 3 < len(lines):
                p1 = parse_percent_line(lines[i + 2])
                p2 = parse_percent_line(lines[i + 3])
                if p1 is not None and p2 is not None:
                    abv_from, abv_to = p1, p2

            rules.append(
                RateRule(
                    country=country,
                    product=product,
                    subtype=current_subtype,
                    rate_eur=rate,
                    unit="EUR/hl",
                    abv_from=abv_from,
                    abv_to=abv_to,
                    vat=vat,
                    source_file=source_file,
                )
            )

        i += 1

    # Fallback: se non ci sono Still/Sparkling ma esiste un'aliquota standard
    if not rules:
        for i, line in enumerate(lines):
            rate = parse_rate_line(line)
            if rate is not None:
                vat = parse_percent_line(lines[i + 1]) if i + 1 < len(lines) else None
                rules.append(
                    RateRule(
                        country=country,
                        product=product,
                        subtype="Standard",
                        rate_eur=rate,
                        unit="EUR/hl",
                        vat=vat,
                        source_file=source_file,
                    )
                )
                break

    return rules


def parse_beer(country: str, source_file: str, lines: List[str]) -> List[RateRule]:
    rules: List[RateRule] = []
    joined = " ".join(lines[:30])
    unit = "EUR/hl_per_alcohol_degree"
    note = ""

    if "°Plato" in joined or "Per °Plato" in joined:
        note = "Fonte TEDB indica °Plato: per la birra inserire il valore Plato nel campo grado/parametro."

    for i, line in enumerate(lines):
        rate = parse_rate_line(line)
        if rate is not None:
            vat = parse_percent_line(lines[i + 1]) if i + 1 < len(lines) else None
            rules.append(
                RateRule(
                    country=country,
                    product="Beer",
                    subtype="Standard",
                    rate_eur=rate,
                    unit=unit,
                    vat=vat,
                    source_file=source_file,
                    note=note,
                )
            )
            break

    return rules


def parse_ethyl(country: str, source_file: str, lines: List[str]) -> List[RateRule]:
    for i, line in enumerate(lines):
        rate = parse_rate_line(line)
        if rate is not None:
            vat = parse_percent_line(lines[i + 1]) if i + 1 < len(lines) else None
            return [
                RateRule(
                    country=country,
                    product="Ethyl alcohol",
                    subtype="Standard",
                    rate_eur=rate,
                    unit="EUR/hl_pure_alcohol",
                    vat=vat,
                    source_file=source_file,
                )
            ]
    return []


def parse_tedb_html_text(filename: str, raw_html: str) -> List[RateRule]:
    lines = html_to_lines_from_text(raw_html)
    country = country_from_file(filename)
    source = filename
    rules: List[RateRule] = []

    beer = find_section(lines, "Beer", ["Wine"])
    if beer:
        rules += parse_beer(country, source, beer)

    wine = find_section(lines, "Wine", ["Fermented beverages other than wine and beer", "Intermediate products", "Ethyl alcohol"])
    if wine:
        rules += parse_wine_like(country, source, wine, "Wine")

    fermented = find_section(lines, "Fermented beverages other than wine and beer", ["Intermediate products", "Ethyl alcohol"])
    if fermented:
        rules += parse_wine_like(country, source, fermented, "Fermented beverages other than wine and beer")

    intermediate = find_section(lines, "Intermediate products", ["Ethyl alcohol"])
    if intermediate:
        rules += parse_wine_like(country, source, intermediate, "Intermediate products")

    ethyl = find_section(lines, "Ethyl alcohol", ["Footnote", "Contatta la Commissione europea", "Contact the European Commission"])
    if ethyl:
        rules += parse_ethyl(country, source, ethyl)

    dedup = {}
    for r in rules:
        key = (r.country, r.product, r.subtype, r.rate_eur, r.unit, r.abv_from, r.abv_to)
        dedup[key] = r

    return list(dedup.values())


@st.cache_data(show_spinner=False)
def load_rates_from_repo() -> List[dict]:
    folder = Path(__file__).resolve().parent
    rules: List[RateRule] = []

    for path in sorted(folder.glob("*.htm*")):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            rules.extend(parse_tedb_html_text(path.name, raw))
        except Exception:
            continue

    return [asdict(r) for r in rules]


def parse_uploaded_files(uploaded_files) -> List[dict]:
    rules: List[RateRule] = []

    for f in uploaded_files or []:
        raw = f.read().decode("utf-8", errors="ignore")
        rules.extend(parse_tedb_html_text(f.name, raw))

    return [asdict(r) for r in rules]


def matches_abv(rule: dict, abv: float) -> bool:
    lo = rule.get("abv_from")
    hi = rule.get("abv_to")

    if lo is None and hi is None:
        return True

    lo_ok = True if lo is None else (abv > lo or (abs(abv - lo) < 1e-9 and lo == 0))
    hi_ok = True if hi is None else abv <= hi + 1e-9

    return lo_ok and hi_ok


def find_applicable_rule(rules: List[dict], country: str, product: str, subtype: str, abv: float) -> Optional[dict]:
    candidates = [
        r for r in rules
        if r["country"] == country and r["product"] == product and r["subtype"] == subtype
    ]

    abv_matches = [r for r in candidates if matches_abv(r, abv)]

    if abv_matches:
        abv_matches.sort(key=lambda r: (r.get("abv_from") is None and r.get("abv_to") is None, r["rate_eur"]))
        return abv_matches[0]

    return candidates[0] if candidates else None


def calculate_excise(rule: dict, bottles: float, liters_per_bottle: float, abv: float) -> Tuple[float, float]:
    total_liters = bottles * liters_per_bottle
    hl = total_liters / 100.0
    unit = rule["unit"]
    rate = float(rule["rate_eur"])

    if unit == "EUR/hl":
        excise = hl * rate
    elif unit == "EUR/hl_per_alcohol_degree":
        excise = hl * abv * rate
    elif unit == "EUR/hl_pure_alcohol":
        pure_alcohol_hl = (total_liters * (abv / 100.0)) / 100.0
        excise = pure_alcohol_hl * rate
    else:
        raise ValueError(f"Unità non supportata: {unit}")

    return total_liters, excise


def rules_to_df(rules: List[dict]) -> pd.DataFrame:
    if not rules:
        return pd.DataFrame(columns=[
            "country", "product", "subtype", "rate_eur", "unit",
            "abv_from", "abv_to", "vat", "source_file", "note"
        ])
    return pd.DataFrame(rules)


def main():
    st.set_page_config(page_title="Calcolatore accise UE", layout="wide")

    st.title("Calcolatore accise UE da HTML TEDB")
    st.caption("Versione Streamlit: legge gli HTML TEDB caricati nel repository oppure caricati manualmente dalla sidebar.")

    repo_rules = load_rates_from_repo()

    with st.sidebar:
        st.header("Dati aliquote")
        st.write(f"Aliquote trovate negli HTML del repository: **{len(repo_rules)}**")

        uploaded = st.file_uploader(
            "Carica HTML TEDB aggiuntivi",
            type=["html", "htm"],
            accept_multiple_files=True,
        )

        use_uploaded_only = st.checkbox("Usa solo i file caricati qui", value=False)

        if st.button("Ricarica dati repository"):
            st.cache_data.clear()
            st.rerun()

    uploaded_rules = parse_uploaded_files(uploaded) if uploaded else []
    rules = uploaded_rules if use_uploaded_only else repo_rules + uploaded_rules

    # Dedup globale
    dedup = {}
    for r in rules:
        key = (r["country"], r["product"], r["subtype"], r["rate_eur"], r["unit"], r.get("abv_from"), r.get("abv_to"))
        dedup[key] = r
    rules = list(dedup.values())

    if not rules:
        st.error("Nessuna aliquota trovata. Carica gli HTML TEDB nel repository o usa il caricamento manuale dalla sidebar.")
        st.stop()

    st.success(f"Aliquote disponibili: {len(rules)}")

    if "cart" not in st.session_state:
        st.session_state.cart = []

    countries = sorted({r["country"] for r in rules})

    st.subheader("Inserimento prodotto")

    c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1.6, 1, 0.9, 0.9, 1])

    with c1:
        country = st.selectbox("Destinazione", countries)

    products = sorted({r["product"] for r in rules if r["country"] == country})

    with c2:
        product = st.selectbox("Tipologia prodotto", products)

    subtypes = sorted({r["subtype"] for r in rules if r["country"] == country and r["product"] == product})

    with c3:
        subtype = st.selectbox("Sottotipo", subtypes)

    with c4:
        abv = st.number_input("Grado / parametro", min_value=0.0, value=12.0, step=0.1, format="%.2f")

    with c5:
        bottles = st.number_input("N. bottiglie", min_value=0.0, value=1.0, step=1.0, format="%.2f")

    with c6:
        liters_per_bottle = st.number_input("Litri/bottiglia", min_value=0.0, value=0.75, step=0.05, format="%.3f")

    rule = find_applicable_rule(rules, country, product, subtype, abv)

    if rule:
        lo = rule.get("abv_from")
        hi = rule.get("abv_to")
        fascia = "-"
        if lo is not None or hi is not None:
            fascia = f"> {lo if lo is not None else '-∞'}% e ≤ {hi if hi is not None else '+∞'}%"
        st.info(
            f"Aliquota selezionata: **{rule['rate_eur']:.4f} {rule['unit']}** | "
            f"Fascia ABV: **{fascia}** | Fonte: `{rule['source_file']}`"
        )
        if rule.get("note"):
            st.warning(rule["note"])
    else:
        st.warning("Nessuna aliquota trovata per la combinazione scelta.")

    add_col, clear_col = st.columns([1, 5])

    with add_col:
        if st.button("Aggiungi prodotto", type="primary", use_container_width=True):
            if not rule:
                st.error("Nessuna aliquota applicabile.")
            else:
                total_liters, excise = calculate_excise(rule, bottles, liters_per_bottle, abv)
                st.session_state.cart.append({
                    "Destinazione": country,
                    "Prodotto": product,
                    "Sottotipo": subtype,
                    "Grado/parametro": round(abv, 2),
                    "Bottiglie": round(bottles, 2),
                    "Litri/bottiglia": round(liters_per_bottle, 3),
                    "Litri totali": round(total_liters, 2),
                    "Aliquota": round(rule["rate_eur"], 4),
                    "Unità": rule["unit"],
                    "Accisa EUR": round(excise, 2),
                    "Fonte": rule["source_file"],
                })
                st.rerun()

    with clear_col:
        if st.button("Svuota prodotti"):
            st.session_state.cart = []
            st.rerun()

    st.subheader("Prodotti da calcolare")

    if st.session_state.cart:
        cart_df = pd.DataFrame(st.session_state.cart)
        st.dataframe(cart_df, use_container_width=True, hide_index=True)

        total = cart_df["Accisa EUR"].sum()
        st.metric("Totale accise", f"{total:.2f} €")

        csv_data = cart_df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
        st.download_button(
            "Scarica calcolo CSV",
            data=csv_data,
            file_name="calcolo_accise.csv",
            mime="text/csv",
        )
    else:
        st.write("Nessun prodotto aggiunto.")

    st.divider()
    st.subheader("Aliquote importate")

    df_rules = rules_to_df(rules)
    st.dataframe(df_rules, use_container_width=True, hide_index=True)

    csv_rules = df_rules.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")
    st.download_button(
        "Scarica database aliquote CSV",
        data=csv_rules,
        file_name="aliquote_importate.csv",
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
