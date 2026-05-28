#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from parser_v5 import (
    RateRule,
    parse_folder,
    parse_tedb_html_text,
    rules_to_dicts,
    matches_abv,
    calculate_excise,
)


@st.cache_data(show_spinner=False)
def load_rates_from_repo() -> list[dict]:
    folder = Path(__file__).resolve().parent
    rules = parse_folder(folder)
    return rules_to_dicts(rules)


def parse_uploaded_files(uploaded_files) -> list[dict]:
    all_rules: list[RateRule] = []
    for f in uploaded_files or []:
        raw = f.read().decode("utf-8", errors="ignore")
        all_rules.extend(parse_tedb_html_text(f.name, raw))
    return rules_to_dicts(all_rules)


def dedup_dict_rules(rules: list[dict]) -> list[dict]:
    out = {}
    for r in rules:
        key = (
            r["country"], r["product"], r["subtype"], r["rate_eur"], r["unit"],
            r.get("abv_from"), r.get("abv_to"), r.get("vat"), r.get("rate_type")
        )
        out[key] = r
    return list(out.values())


def select_best_rule(rules: list[dict], country: str, product: str, subtype: str, abv: float) -> dict | None:
    candidates = [
        r for r in rules
        if r["country"] == country and r["product"] == product and r["subtype"] == subtype
    ]
    matches = [r for r in candidates if matches_abv(r, abv)]
    if not matches:
        return None
    matches.sort(key=lambda r: (
        r.get("abv_from") is None and r.get("abv_to") is None,
        0 if "reduced" in (r.get("rate_type") or "").lower() else 1,
        float(r["rate_eur"])
    ))
    return matches[0]


def range_label(r: dict) -> str:
    lo = r.get("abv_from")
    hi = r.get("abv_to")
    if lo is None and hi is None:
        return "-"
    left = "-∞" if lo is None else f">{lo:g}%"
    right = "+∞" if hi is None else f"≤{hi:g}%"
    return f"{left} / {right}"


def main():
    st.set_page_config(page_title="Calcolatore accise UE", layout="wide")
    st.title("Calcolatore accise UE da HTML TEDB")
    st.caption("Parser v5: include birra, vino, fermentati, intermedi ed etilico. Per la birra il campo “Grado/parametro” può essere ABV o °Plato secondo l’unità mostrata.")

    repo_rules = load_rates_from_repo()

    with st.sidebar:
        st.header("Dati aliquote")
        st.write(f"Aliquote nel repository: **{len(repo_rules)}**")
        uploaded = st.file_uploader("Carica HTML TEDB aggiuntivi", type=["html", "htm"], accept_multiple_files=True)
        use_uploaded_only = st.checkbox("Usa solo i file caricati qui", value=False)
        if st.button("Ricarica dati repository"):
            st.cache_data.clear()
            st.rerun()

    uploaded_rules = parse_uploaded_files(uploaded) if uploaded else []
    rules = uploaded_rules if use_uploaded_only else repo_rules + uploaded_rules
    rules = dedup_dict_rules(rules)

    if not rules:
        st.error("Nessuna aliquota trovata. Carica HTML TEDB nella root del repository o usa l'upload laterale.")
        st.stop()

    st.success(f"Aliquote disponibili: {len(rules)}")

    if "cart" not in st.session_state:
        st.session_state.cart = []

    countries = sorted({r["country"] for r in rules})

    st.subheader("Inserimento prodotto")
    c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1.8, 1.1, 0.9, 0.9, 1])

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

    rule = select_best_rule(rules, country, product, subtype, abv)

    if rule:
        st.info(
            f"Aliquota selezionata: **{rule['rate_eur']:.4f} {rule['unit']}** | "
            f"Fascia: **{range_label(rule)}** | Tipo: **{rule.get('rate_type','')}** | Fonte: `{rule['source_file']}`"
        )
        if rule.get("unit") == "EUR/hl_per_plato":
            st.warning("Per questa birra TEDB usa °Plato: inserisci il valore °Plato nel campo Grado/parametro.")
        elif rule.get("unit") == "EUR/hl_per_alcohol_degree":
            st.warning("Per questa birra TEDB usa €/hl/°Alcohol: inserisci il grado alcolico nel campo Grado/parametro.")
        if rule.get("note"):
            st.caption(f"Nota: {rule['note']}")
    else:
        st.warning("Nessuna aliquota compatibile con combinazione e grado/parametro indicati.")

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
                    "Aliquota": round(float(rule["rate_eur"]), 4),
                    "Unità": rule["unit"],
                    "Fascia": range_label(rule),
                    "Tipo aliquota": rule.get("rate_type", ""),
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
        st.metric("Totale accise", f"{cart_df['Accisa EUR'].sum():.2f} €")
        st.download_button("Scarica calcolo CSV", data=cart_df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"), file_name="calcolo_accise.csv", mime="text/csv")
    else:
        st.write("Nessun prodotto aggiunto.")

    st.divider()
    st.subheader("Aliquote importate")
    df = pd.DataFrame(rules)
    preferred_cols = ["country", "product", "subtype", "rate_eur", "unit", "abv_from", "abv_to", "vat", "rate_type", "source_file", "note"]
    df = df[[c for c in preferred_cols if c in df.columns]]
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("Scarica database aliquote CSV", data=df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"), file_name="aliquote_importate_v4.csv", mime="text/csv")


if __name__ == "__main__":
    main()
