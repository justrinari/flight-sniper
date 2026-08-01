"""IATA-коды авиакомпаний, реально встречающихся на FRU/ALA → HKT/DPS.

Полный справочник Travelpayouts тянуть не стали: в дайджест попадают
единицы перевозчиков, а неизвестный код читается как код и это нормально.
"""

from __future__ import annotations

from typing import Optional

NAMES = {
    "KC": "Air Astana",
    "QH": "Jazeera",
    "HY": "Uzbekistan Airways",
    "TK": "Turkish Airlines",
    "FZ": "flydubai",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "SU": "Aeroflot",
    "S7": "S7 Airlines",
    "U6": "Ural Airlines",
    "PC": "Pegasus",
    "GA": "Garuda",
    "SQ": "Singapore Airlines",
    "MH": "Malaysia Airlines",
    "AK": "AirAsia",
    "D7": "AirAsia X",
    "TG": "Thai Airways",
    "FD": "Thai AirAsia",
    "CZ": "China Southern",
    "CA": "Air China",
    "MU": "China Eastern",
    "HU": "Hainan Airlines",
    "3U": "Sichuan Airlines",
    "KE": "Korean Air",
    "OZ": "Asiana",
    "VN": "Vietnam Airlines",
    "VJ": "VietJet",
    "6E": "IndiGo",
    "AI": "Air India",
    "UK": "Vistara",
    "ZF": "Azur Air",
    "N4": "Nordwind",
    "7R": "RusLine",
    "IO": "IrAero",
    "J2": "AZAL",
    "GF": "Gulf Air",
    "WY": "Oman Air",
    "SV": "Saudia",
    "ET": "Ethiopian",
    "PS": "Ukraine Intl",
    "LO": "LOT",
    "OS": "Austrian",
    "LH": "Lufthansa",
    "AF": "Air France",
    "KL": "KLM",
    "BA": "British Airways",
    "CX": "Cathay Pacific",
    "PG": "Bangkok Airways",
    "ID": "Batik Air",
    "QZ": "Indonesia AirAsia",
    "JT": "Lion Air",
    "SL": "Thai Lion Air",
    "XJ": "Thai AirAsia X",
}

PLACEHOLDER = "—"


def name(code: Optional[str]) -> str:
    if not code:
        return PLACEHOLDER
    return NAMES.get(code.strip().upper(), code.strip().upper())
