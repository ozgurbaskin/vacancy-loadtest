from vacancy_loadtest.data import COUNTRIES, DIVISIONS, UPDATABLE_FIELDS, VacancyFactory


def test_titles_are_unique_even_with_a_seed():
    factory = VacancyFactory(seed=1)
    titles = {factory.create_request().Title for _ in range(500)}
    assert len(titles) == 500


def test_create_request_is_valid():
    request = VacancyFactory(seed=7).create_request()
    assert request.Title
    assert request.Description
    assert request.Division in DIVISIONS
    assert request.Country in COUNTRIES


def test_update_request_changes_one_to_three_fields():
    factory = VacancyFactory(seed=3)
    for _ in range(300):
        request, expected = factory.update_request("abc123")
        assert request.Id == "abc123"
        assert 1 <= len(expected) <= 3
        for field in UPDATABLE_FIELDS:
            if field in expected:
                assert request.HasField(field)
                assert getattr(request, field) == expected[field]
            else:
                assert not request.HasField(field)


def test_update_request_honours_field_count():
    request, expected = VacancyFactory(seed=3).update_request("id", field_count=5)
    assert len(expected) == len(UPDATABLE_FIELDS)
    assert all(request.HasField(f) for f in UPDATABLE_FIELDS)


def test_updates_never_send_proto3_zero_values():
    factory = VacancyFactory(seed=11)
    for _ in range(500):
        _, expected = factory.update_request("id")
        assert expected.get("Division", 1) != 0


def test_views_is_never_updated():
    assert "Views" not in UPDATABLE_FIELDS


def test_seed_reproduces_content_but_not_title_suffix():
    first, second = VacancyFactory(seed=42), VacancyFactory(seed=42)
    a, b = first.create_request(), second.create_request()
    assert a.Title.rsplit(" [", 1)[0] == b.Title.rsplit(" [", 1)[0]
    assert a.Title != b.Title
    assert (a.Description, a.Division, a.Country) == (b.Description, b.Division, b.Country)
