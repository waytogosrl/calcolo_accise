#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Iterable

from bs4 import BeautifulSoup, Tag


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
    rate_type: str = "Standard Rate"
    source_file: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


COUNTRY_ALIASES = {
    "austria": "Austria", "belgio": "Belgio", "be": "Belgio",
    "germania": "Germania", "de": "Germania",
    "francia": "Francia", "france": "Francia", "fr": "Francia",
    "svezia": "Svezia", "se": "Svezia",
    "paesi bassi": "Paesi Bassi", "netherlands": "Paesi Bassi", "nl": "Paesi Bassi",
    "danimarca": "Danimarca", "denmark": "Danimarca", "dk": "Danimarca",
    "finlandia": "Finlandia", "finland": "Finlandia", "fi": "Finlandia",
    "lituania": "Lituania", "lussemburgo": "Lussemburgo",
    "rep ceca": "Rep Ceca", "slovacchia": "Slovacchia",
    "slovenia": "Slovenia", "spagna": "Spagna",
}


SECTION_MAP = {
    "wines-tables": ("Wine", "Wine"),
    "fermented-tables": ("Fermented beverages other than wine and beer", "Fermented beverages other than wine and beer"),
    "intermediate-tables": ("Intermediate products", "Intermediate products"),
    "ethyl-tables": ("Ethyl alcohol", "Ethyl alcohol"),
}


def country_from_file(filename: str) -> str:
    stem = Path(filename).stem
    normalized = stem.replace("_", " ").replace("-", " ").strip().lower()
    normalized = re.sub(r"\(\d+\)$", "", normalized).strip()
    return COUNTRY_ALIASES.get(normalized, normalized.title())


def clean_number(text: str) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().replace("\xa0", " ")
    match = re.search(r"[-+]?\d[\d\s.,']*", s)
    if not match:
        return None
    s = match.group(0).replace("'", "").replace(" ", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s and s.rfind(",") > s.rfind("."):
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def is_rate_line(line: str) -> bool:
    if not line:
        return False
    lower = line.lower()
    if "min." in lower or "minimum" in lower or "per hl" in lower or "per °" in lower or ">= " in lower:
        return False
    return bool(re.fullmatch(r"\s*\d[\d\s.,']*\s*EUR\s*", line.strip(), flags=re.I))


def parse_rate_line(line: str) -> Optional[float]:
    if not is_rate_line(line):
        return None
    return clean_number(line)


def is_percent_line(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*%\s*", line or ""))


def parse_percent_line(line: str) -> Optional[float]:
    if not is_percent_line(line):
        return None
    return clean_number(line)


def normalize_lines(node: Tag) -> List[str]:
    return [
        re.sub(r"\s+", " ", x).strip()
        for x in node.get_text("\n").splitlines()
        if re.sub(r"\s+", " ", x).strip()
    ]


def block_type_from_line(line: str) -> Optional[str]:
    l = (line or "").strip().lower()
    if l.startswith("standard rate"):
        return "Standard Rate"
    if l.startswith("reduced rate"):
        return line.strip()
    return None


def title_cap_abv(line: str) -> Tuple[Optional[float], Optional[float]]:
    l = (line or "").lower().replace(",", ".")
    m = re.search(r"<=\s*([0-9]+(?:\.[0-9]+)?)\s*%", l)
    if m:
        return None, float(m.group(1))
    m = re.search(r"not more than\s*([0-9]+(?:\.[0-9]+)?)\s*%", l)
    if m:
        return None, float(m.group(1))
    m = re.search(r"low alcohol.*?([0-9]+(?:\.[0-9]+)?)\s*%", l)
    if m:
        return None, float(m.group(1))
    return None, None


def infer_bounds_from_note(note: str) -> Tuple[Optional[float], Optional[float]]:
    t = (note or "").lower().replace(",", ".").replace("≤", "<=").replace("≥", ">=")
    m = re.search(r"<=\s*([0-9]+(?:\.[0-9]+)?)\s*%", t)
    if m:
        return None, float(m.group(1))
    m = re.search(r"(?:>|above|more than)\s*([0-9]+(?:\.[0-9]+)?)\s*%", t)
    if m:
        return float(m.group(1)), None
    m = re.search(r"<\s*([0-9]+(?:\.[0-9]+)?)\s*%", t)
    if m:
        return None, float(m.group(1))
    nums = [float(x) for x in re.findall(r"[0-9]+(?:\.[0-9]+)?", t)]
    if "above" in t and ("not more than" in t or "not exceeding" in t) and len(nums) >= 2:
        return nums[0], nums[1]
    return None, None


def infer_bounds_from_percents(percent_values: List[float], block_abv_to: Optional[float], note: str, rate_type: str) -> Tuple[Optional[float], Optional[float]]:
    if len(percent_values) >= 2:
        return percent_values[0], percent_values[1]

    nlo, nhi = infer_bounds_from_note(note)
    if nlo is not None or nhi is not None:
        if block_abv_to is not None and "reduced" in rate_type.lower() and nhi is not None and abs(nhi - block_abv_to) > 1e-9:
            return nlo, block_abv_to
        return nlo, nhi

    if len(percent_values) == 1:
        value = percent_values[0]
        if block_abv_to is not None and "reduced" in rate_type.lower():
            return None, value
        return value, None

    if block_abv_to is not None and "reduced" in rate_type.lower():
        return None, block_abv_to

    return None, None


def detect_unit(section_text: str, product: str) -> str:
    t = (section_text or "").lower()
    p = (product or "").lower()
    if p == "beer":
        if "°plato" in t:
            return "EUR/hl_per_plato"
        if "°alcohol" in t:
            return "EUR/hl_per_alcohol_degree"
        return "EUR/hl_per_alcohol_degree"
    if "ethyl alcohol" in p:
        return "EUR/hl_pure_alcohol"
    return "EUR/hl"


def split_blocks(lines: List[str]) -> List[Tuple[str, List[str]]]:
    blocks: List[Tuple[str, List[str]]] = []
    current_type = None
    current_lines: List[str] = []
    for line in lines:
        bt = block_type_from_line(line)
        if bt:
            if current_type is not None:
                blocks.append((current_type, current_lines))
            current_type = bt
            current_lines = [line]
        else:
            if current_type is not None:
                current_lines.append(line)
    if current_type is not None:
        blocks.append((current_type, current_lines))
    return blocks


def parse_entries_for_subtype(lines, start_i, stop_markers, block_type, block_abv_to, default_subtype, country, product, unit, source_file):
    rules: List[RateRule] = []
    i = start_i
    while i < len(lines):
        line = lines[i]
        if line in stop_markers or block_type_from_line(line):
            break
        rate = parse_rate_line(line)
        if rate is None:
            i += 1
            continue

        vat = None
        if i + 1 < len(lines) and is_percent_line(lines[i + 1]):
            vat = parse_percent_line(lines[i + 1])
            i += 2
        else:
            i += 1

        abv_values: List[float] = []
        while i < len(lines) and is_percent_line(lines[i]):
            v = parse_percent_line(lines[i])
            if v is not None:
                abv_values.append(v)
            i += 1

        note_parts = []
        while i < len(lines):
            if lines[i] in stop_markers or block_type_from_line(lines[i]) or is_rate_line(lines[i]):
                break
            if "additional information" not in lines[i].lower():
                note_parts.append(lines[i])
            i += 1

        note = " | ".join(note_parts).strip()
        abv_from, abv_to = infer_bounds_from_percents(abv_values, block_abv_to, note, block_type)
        if abv_from is None and abv_to is None:
            nlo, nhi = infer_bounds_from_note(note)
            abv_from, abv_to = nlo, nhi

        rules.append(RateRule(country, product, default_subtype, rate, unit, abv_from, abv_to, vat, block_type, source_file, note))
    return rules, i


def parse_section_lines(lines: List[str], country: str, product: str, source_file: str) -> List[RateRule]:
    if not lines:
        return []
    section_text = " ".join(lines)
    unit = detect_unit(section_text, product)
    rules: List[RateRule] = []
    has_subtypes = any(x in lines for x in ("Still", "Sparkling"))

    for block_type, block_lines in split_blocks(lines):
        _, block_abv_to = title_cap_abv(block_type)
        if has_subtypes:
            stop_markers = {"Still", "Sparkling"}
            i = 0
            while i < len(block_lines):
                line = block_lines[i]
                if line in stop_markers:
                    parsed, new_i = parse_entries_for_subtype(
                        block_lines, i + 1, stop_markers, block_type, block_abv_to,
                        line, country, product, unit, source_file
                    )
                    rules.extend(parsed)
                    i = max(new_i, i + 1)
                    continue
                i += 1
        else:
            subtype = "Standard"
            if "low alcohol" in block_type.lower():
                subtype = "Low alcohol"
            elif "small distilleries" in block_type.lower() or "small breweries" in block_type.lower():
                subtype = "Small producer"
            elif "reduced" in block_type.lower():
                subtype = "Reduced"
            parsed, _ = parse_entries_for_subtype(block_lines, 0, set(), block_type, block_abv_to, subtype, country, product, unit, source_file)
            rules.extend(parsed)

    postprocess_ranges(rules)
    return deduplicate(rules)


def parse_beer_lines(lines: List[str], country: str, source_file: str) -> List[RateRule]:
    if not lines:
        return []

    text = " ".join(lines).lower()
    unit = "EUR/hl_per_plato" if "°plato" in text else "EUR/hl_per_alcohol_degree"
    rules: List[RateRule] = []

    for block_type, block_lines in split_blocks(lines):
        _, block_abv_to = title_cap_abv(block_type)

        subtype = "Standard"
        if "low alcohol" in block_type.lower():
            subtype = "Low alcohol"
        elif "independent small" in block_type.lower() or "small breweries" in block_type.lower():
            subtype = "Small brewery"
        elif "reduced" in block_type.lower():
            subtype = "Reduced"

        i = 0
        while i < len(block_lines):
            rate = parse_rate_line(block_lines[i])
            if rate is None:
                i += 1
                continue

            vat = None
            if i + 1 < len(block_lines) and is_percent_line(block_lines[i + 1]):
                vat = parse_percent_line(block_lines[i + 1])
                i += 2
            else:
                i += 1

            percents = []
            while i < len(block_lines) and is_percent_line(block_lines[i]):
                v = parse_percent_line(block_lines[i])
                if v is not None:
                    percents.append(v)
                i += 1

            note_parts = []
            while i < len(block_lines):
                if block_type_from_line(block_lines[i]) or is_rate_line(block_lines[i]):
                    break
                # Stop when a non-rate structural subheading starts; keep notes otherwise.
                if block_lines[i] in {"Applicable for Independent small breweries only"}:
                    break
                if "additional information" not in block_lines[i].lower():
                    note_parts.append(block_lines[i])
                i += 1

            note = " | ".join(note_parts).strip()
            abv_from, abv_to = infer_bounds_from_percents(percents, block_abv_to, note, block_type)

            rules.append(RateRule(
                country=country,
                product="Beer",
                subtype=subtype,
                rate_eur=rate,
                unit=unit,
                abv_from=abv_from,
                abv_to=abv_to,
                vat=vat,
                rate_type=block_type,
                source_file=source_file,
                note=note,
            ))

            # For beer, usually the first standard/low-alcohol rate is the main one. 
            # Avoid parsing long small-brewery tables unless explicitly captured as Small brewery block.
            if subtype in {"Standard", "Low alcohol"}:
                break

    return deduplicate(rules)


def parse_ethanol_lines(lines: List[str], country: str, source_file: str) -> List[RateRule]:
    """
    Parser dedicato per Ethyl alcohol / spirits.
    TEDB qui non usa Still/Sparkling: ogni blocco contiene una o più aliquote seguite da VAT e note.
    Base di calcolo: EUR/hl_pure_alcohol.
    """
    unit = "EUR/hl_pure_alcohol"
    rules: List[RateRule] = []

    for block_type, block_lines in split_blocks(lines):
        subtype = "Standard"
        if "small distilleries" in block_type.lower():
            subtype = "Small distillery"
        elif "low strength" in block_type.lower():
            subtype = "Low strength / particular regions"
        elif "reduced" in block_type.lower():
            subtype = "Reduced"

        # Salta la riga titolo del blocco; poi cerca tutte le aliquote vere.
        i = 1 if block_lines and block_type_from_line(block_lines[0]) else 0

        while i < len(block_lines):
            rate = parse_rate_line(block_lines[i])
            if rate is None:
                i += 1
                continue

            vat = None
            if i + 1 < len(block_lines) and is_percent_line(block_lines[i + 1]):
                vat = parse_percent_line(block_lines[i + 1])
                i += 2
            else:
                i += 1

            note_parts = []
            while i < len(block_lines):
                if is_rate_line(block_lines[i]) or block_type_from_line(block_lines[i]):
                    break
                if "additional information" not in block_lines[i].lower():
                    note_parts.append(block_lines[i])
                i += 1

            note = " | ".join(note_parts).strip()
            abv_from, abv_to = infer_bounds_from_note(note)

            rules.append(
                RateRule(
                    country=country,
                    product="Ethyl alcohol / spirits",
                    subtype=subtype,
                    rate_eur=rate,
                    unit=unit,
                    abv_from=abv_from,
                    abv_to=abv_to,
                    vat=vat,
                    rate_type=block_type,
                    source_file=source_file,
                    note=note,
                )
            )

    return deduplicate(rules)


def postprocess_ranges(rules: List[RateRule]) -> None:
    by_key: Dict[Tuple[str, str, str], List[RateRule]] = {}
    for r in rules:
        by_key.setdefault((r.country, r.product, r.subtype), []).append(r)
    for key, group in by_key.items():
        caps = [r.abv_to for r in group if r.abv_to is not None and "reduced" in r.rate_type.lower()]
        if not caps:
            continue
        cap = max(caps)
        for r in group:
            if "standard" in r.rate_type.lower() and r.abv_from is None and r.abv_to is None:
                r.abv_from = cap
                if r.note:
                    r.note += " | "
                r.note += f"Standard rate inferred as > {cap:g}% because a reduced rate exists up to {cap:g}%."
    for r in rules:
        if r.abv_from is not None and r.abv_to is None and abs(r.abv_from - 8.51) < 1e-9:
            r.abv_from = 8.5


def deduplicate(rules: List[RateRule]) -> List[RateRule]:
    out: Dict[Tuple, RateRule] = {}
    for r in rules:
        key = (r.country, r.product, r.subtype, r.rate_eur, r.unit, r.abv_from, r.abv_to, r.vat, r.rate_type)
        out[key] = r
    return list(out.values())


def parse_tedb_html_text(filename: str, raw_html: str) -> List[RateRule]:
    soup = BeautifulSoup(raw_html, "html.parser")
    country = country_from_file(filename)
    rules: List[RateRule] = []

    # Beer dedicated parser
    beer_nodes = soup.find_all(attrs={"data-testid": "beer-tables"})
    for section in beer_nodes:
        rules.extend(parse_beer_lines(normalize_lines(section), country, filename))

    # Wine-like and intermediate sections
    for testid, (_, product) in SECTION_MAP.items():
        section_nodes = soup.find_all(attrs={"data-testid": testid})
        for section in section_nodes:
            lines = normalize_lines(section)
            if testid == "ethyl-tables":
                rules.extend(parse_ethanol_lines(lines, country, filename))
            else:
                rules.extend(parse_section_lines(lines, country, product, filename))

    return deduplicate(rules)


def parse_tedb_html_file(path: Path) -> List[RateRule]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return parse_tedb_html_text(path.name, raw)


def parse_folder(folder: Path) -> List[RateRule]:
    rules: List[RateRule] = []
    for path in sorted(folder.glob("*.htm*")):
        try:
            rules.extend(parse_tedb_html_file(path))
        except Exception as exc:
            print(f"Errore parsing {path.name}: {exc}")
    return deduplicate(rules)


def matches_abv(rule: dict | RateRule, abv: float) -> bool:
    lo = rule.abv_from if isinstance(rule, RateRule) else rule.get("abv_from")
    hi = rule.abv_to if isinstance(rule, RateRule) else rule.get("abv_to")
    if lo is None and hi is None:
        return True
    lo_ok = True if lo is None else (abv > float(lo) + 1e-9 or (abs(abv - float(lo)) < 1e-9 and float(lo) == 0))
    hi_ok = True if hi is None else abv <= float(hi) + 1e-9
    return lo_ok and hi_ok


def calculate_excise(rule: dict | RateRule, bottles: float, liters_per_bottle: float, abv_or_param: float) -> Tuple[float, float]:
    if isinstance(rule, RateRule):
        unit = rule.unit
        rate = float(rule.rate_eur)
    else:
        unit = rule["unit"]
        rate = float(rule["rate_eur"])
    total_liters = bottles * liters_per_bottle
    hl = total_liters / 100.0
    if unit == "EUR/hl":
        excise = hl * rate
    elif unit in {"EUR/hl_per_alcohol_degree", "EUR/hl_per_plato"}:
        excise = hl * abv_or_param * rate
    elif unit == "EUR/hl_pure_alcohol":
        pure_alcohol_hl = (total_liters * (abv_or_param / 100.0)) / 100.0
        excise = pure_alcohol_hl * rate
    else:
        raise ValueError(f"Unità non supportata: {unit}")
    return total_liters, excise


def rules_to_dicts(rules: List[RateRule]) -> List[dict]:
    return [r.to_dict() for r in rules]
