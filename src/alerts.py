"""Подтверждение кандидата свежим запросом к тому же кэшу и текст алерта.

Кэш Aviasales — это чужие поиски за последние ~48 часов, а не оферта: цена
могла устареть. Перед тем как будить пользователя, делаем точечный повторный
запрос по конкретной дате кандидата — если предложение всё ещё в выдаче,
значит его кто-то видел только что.

Подтверждаем тем же кэшем, а не GDS: лучшие цены везут лоукостеры вроде
AirAsia X через посредников (City.Travel и подобные), которых в GDS нет —
GDS отвечал бы «не подтвердилось» именно на лучших находках.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional, Sequence

from src import airlines, digest, fx, rules
from src.models import PriceRecord
from src.sources.aviasales import AviasalesError, fetch_prices_for_dates

LEVEL_BUY = "buy"
LEVEL_UNCONFIRMED = "unconfirmed"

# Насколько подтверждённая цена может отличаться от цены кандидата и всё ещё
# считаться той же самой находкой (кэш немного шевелится между запросами).
CONFIRM_TOLERANCE = 1.1

NOTE_NO_EXACT_MATCH = "точная связка не найдена, сравниваю с лучшей ценой на дату"
NOTE_DISAPPEARED = "предложение исчезло из выдачи кэша"


@dataclass(frozen=True)
class ConfirmResult:
    candidate: rules.Candidate
    level: str
    confirmed_local: Optional[float]  # цена из свежего запроса, в локальной валюте
    confirmed_usd: Optional[float]
    note: str


def _pick_match(
    records: Sequence[PriceRecord], candidate: rules.Candidate
) -> tuple[Optional[PriceRecord], str]:
    """Выбирает запись для сравнения: та же связка, иначе лучшая цена на дату."""
    same_date = [r for r in records if r.depart_date == candidate.depart_date]
    if not same_date:
        return None, ""

    exact = [
        r
        for r in same_date
        if r.airline == candidate.airline and r.transfers == candidate.transfers
    ]
    if exact:
        return min(exact, key=lambda r: r.price_local), ""
    return min(same_date, key=lambda r: r.price_local), NOTE_NO_EXACT_MATCH


def _unconfirmed(candidate: rules.Candidate, note: str) -> ConfirmResult:
    return ConfirmResult(
        candidate=candidate,
        level=LEVEL_UNCONFIRMED,
        confirmed_local=None,
        confirmed_usd=None,
        note=note,
    )


def confirm(session, token, cfg, candidate: rules.Candidate, rates, now) -> ConfirmResult:
    """Точечный повторный запрос к кэшу на дату кандидата.

    Скан не должен падать из-за неудачного подтверждения: любая ошибка
    Aviasales превращается в LEVEL_UNCONFIRMED, а не в исключение.
    """
    try:
        records = fetch_prices_for_dates(
            session,
            token,
            origin=candidate.origin,
            destination=candidate.destination,
            market=candidate.market,
            currency=candidate.currency,
            departure_at=candidate.depart_date,
            return_at=candidate.return_date,
            one_way=cfg.one_way,
            limit=30,
        )
    except AviasalesError as exc:
        return _unconfirmed(candidate, str(exc))

    if not records:
        return _unconfirmed(candidate, NOTE_DISAPPEARED)

    match, note = _pick_match(records, candidate)
    if match is None:
        return _unconfirmed(candidate, NOTE_DISAPPEARED)

    confirmed_local = float(match.price_local)
    # Сравнение в локальной валюте: курс сюда не вмешивается, иначе движение
    # валюты выглядело бы как подорожание билета.
    level = (
        LEVEL_BUY
        if confirmed_local <= candidate.price_local * CONFIRM_TOLERANCE
        else LEVEL_UNCONFIRMED
    )

    confirmed_usd: Optional[float] = None
    try:
        rate = fx.usd_per_unit(rates, candidate.currency)
        markup = cfg.markup_for(candidate.currency)
        confirmed_usd = fx.landed_usd(confirmed_local, rate, markup)
    except fx.FxError:
        confirmed_usd = None

    return ConfirmResult(
        candidate=candidate,
        level=level,
        confirmed_local=confirmed_local,
        confirmed_usd=confirmed_usd,
        note=note,
    )


_GATE_WARNING = "продавец-посредник: поддержка при отмене рейса будет хуже, чем у авиакомпании напрямую"


def render_alert(result: ConfirmResult) -> str:
    c = result.candidate
    route = f"{html.escape(c.origin)}→{html.escape(c.destination)}"
    price = f"${c.landed_usd:.0f}"

    if result.level == LEVEL_BUY:
        header = f"🟢 <b>BUY</b> · {route} <b>{price}</b>"
    else:
        header = f"🔍 {route} <b>{price}</b> — кэш видел, но не подтвердилось"

    lines = [header, ""]

    depart = digest.format_day(c.depart_date)
    if c.return_date:
        ret = digest.format_day(c.return_date)
        lines.append(f"вылет {depart} · возврат {ret} · {c.nights_text}")
    else:
        lines.append(f"вылет {depart} · {c.nights_text}")

    airline_name = html.escape(airlines.name(c.airline))
    transfers_text = digest.format_transfers(c.transfers)
    detail = ", ".join(part for part in (airline_name, transfers_text) if part)
    lines.append(f"{detail}  [{html.escape(c.market)}]")

    if c.gate:
        lines.append(f"продавец: {html.escape(c.gate)} — {_GATE_WARNING}")

    if result.confirmed_local is not None:
        if result.confirmed_usd is not None:
            lines.append(f"в кэше сейчас: ${result.confirmed_usd:.0f}")
        else:
            lines.append(
                f"в кэше сейчас: {result.confirmed_local:.0f} {c.currency.upper()}"
            )

    if result.note:
        lines.append(html.escape(result.note))

    baseline = c.baseline
    lines.append(
        f"медиана {baseline.median:.0f} {c.currency.upper()}, порог "
        f"{baseline.anomaly_threshold:.0f} {c.currency.upper()} (n={baseline.n})"
    )

    if c.search_url:
        lines.append(f'<a href="{html.escape(c.search_url, quote=True)}">поиск</a>')

    if result.level == LEVEL_BUY:
        lines.append("")
        lines.append("Купили — отметьте /bought. Нашли нестыковку в цене — /mismatch.")

    return "\n".join(lines)
