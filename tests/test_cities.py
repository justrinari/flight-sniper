from src import cities


def test_known_code_resolves_to_name():
    assert cities.name("BSZ") == "Бишкек"
    assert cities.name("HKT") == "Пхукет"


def test_lookup_is_case_insensitive():
    assert cities.name("hkt") == "Пхукет"


def test_unknown_code_returns_code_itself():
    assert cities.name("ZZZ") == "ZZZ"


def test_empty_code_returns_placeholder():
    assert cities.name("") == "—"


def test_none_returns_placeholder():
    assert cities.name(None) == "—"


def test_legacy_fru_code_also_resolves_to_bishkek():
    # BSZ — код Бишкека в справочнике Travelpayouts, но FRU может встретиться
    # в старых данных или тестах — тоже должен читаться как Бишкек.
    assert cities.name("FRU") == "Бишкек"


def test_route_builds_arrow_string():
    assert cities.route("BSZ", "HKT") == "Бишкек → Пхукет"


def test_route_with_unknown_code_falls_back_to_code():
    assert cities.route("BSZ", "ZZZ") == "Бишкек → ZZZ"


def test_trip_airports_have_names():
    assert cities.name("PQC") == "Фукуок"
    assert cities.name("CXR") == "Нячанг"
    assert cities.name("DAD") == "Дананг"
    assert cities.name("MNL") == "Манила"
    assert cities.name("CEB") == "Себу"
    assert cities.name("MPH") == "Боракай"


def test_country_of_airport():
    assert cities.country("SGN") == "Вьетнам"
    assert cities.country("mnl") == "Филиппины"
    assert cities.country("XXX") is None
    assert cities.country(None) is None
