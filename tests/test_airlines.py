from src import airlines


def test_known_code_resolves_to_name():
    assert airlines.name("KC") == "Air Astana"


def test_lookup_is_case_insensitive():
    assert airlines.name("kc") == "Air Astana"


def test_unknown_code_returns_code_itself():
    assert airlines.name("ZZ") == "ZZ"


def test_empty_code_returns_placeholder():
    assert airlines.name("") == "—"


def test_none_returns_placeholder():
    assert airlines.name(None) == "—"


def test_regional_low_cost_carriers_have_names():
    assert airlines.name("5J") == "Cebu Pacific"
    assert airlines.name("Z2") == "Philippines AirAsia"
    assert airlines.name("VZ") == "Thai Vietjet"
    assert airlines.name("TR") == "Scoot"
