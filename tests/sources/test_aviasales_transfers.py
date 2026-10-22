from src.sources import aviasales


def _link(chain, airline="AK"):
    return (
        f"https://www.aviasales.ru/search/X?t={airline}1800292800180034440000092"
        f"0{chain}_7ae2842d684dcc859121a513a35accc1_7042&search_date=21102026"
    )


def test_single_transfer():
    assert aviasales.transfer_points(_link("DPSKULHKT"), transfers=1) == [("KUL", "KUL")]


def test_two_transfers():
    assert aviasales.transfer_points(_link("CXRSGNMNLMPH"), transfers=2) == [
        ("SGN", "SGN"),
        ("MNL", "MNL"),
    ]


def test_airport_change_inside_one_transfer():
    # Прилёт в Суварнабхуми, вылет из Дон Мыанга.
    assert aviasales.transfer_points(_link("HKTBKKDMKSGN", "FD"), transfers=1) == [
        ("BKK", "DMK")
    ]


def test_airline_code_with_digit():
    assert aviasales.transfer_points(_link("CEBMNLDPS", "5J"), transfers=1) == [
        ("MNL", "MNL")
    ]


def test_direct_flight_has_no_transfer_points():
    assert aviasales.transfer_points(_link("HKTSGN", "VJ"), transfers=0) == []


def test_unparseable_link_gives_none():
    assert aviasales.transfer_points("https://www.aviasales.ru/search/x", transfers=1) is None
    assert aviasales.transfer_points("", transfers=1) is None
    # Число аэропортов не сходится с числом пересадок.
    assert aviasales.transfer_points(_link("DPSKULHKT"), transfers=2) is None


def test_ticket_times_are_local_departure_and_arrival():
    from datetime import datetime

    link = "https://www.aviasales.kg/search/SGN2801MNL1?t=TR18011511001801247400001545SGNSINMNL_db1f_9704&x=1"
    assert aviasales.ticket_times(link) == (
        datetime(2027, 1, 28, 15, 45),
        datetime(2027, 1, 29, 18, 30),
    )
    five_j = "https://www.aviasales.kg/search/MNL3101DPS1?t=5J18013671001801381800000245MNLDPS_1cc3_8391&x=1"
    assert aviasales.ticket_times(five_j) == (
        datetime(2027, 1, 31, 3, 45),
        datetime(2027, 1, 31, 7, 50),
    )


def test_ticket_times_unparseable_gives_none():
    assert aviasales.ticket_times("https://www.aviasales.ru/search/x") is None
    assert aviasales.ticket_times("") is None
